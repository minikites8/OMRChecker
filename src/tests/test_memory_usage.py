"""Bounded image, upload-queue and download memory regressions."""
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import fitz
import numpy as np
import pytest

from src.core import ImageInstanceOps
from src.utils.image import ImageUtils


def config(page=None, dpi=72, level=1):
    return SimpleNamespace(pdf_params=SimpleNamespace(pdf_dpi=dpi, pdf_page=page),
                           outputs=SimpleNamespace(save_image_level=level))


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        for index in range(3):
            page = doc.new_page(width=144, height=72)
            page.insert_text((10, 30), str(index + 1))
        doc.save(path)
    return path


def test_pdf_stream_renders_only_requested_page_and_closes_early(pdf, monkeypatch):
    doc = fitz.open(pdf)
    render = fitz.Page.get_pixmap
    rendered = []
    def tracked(page, *args, **kwargs):
        rendered.append(page.number)
        return render(page, *args, **kwargs)
    monkeypatch.setattr(fitz, "open", lambda _: doc)
    monkeypatch.setattr(fitz.Page, "get_pixmap", tracked)
    images = ImageUtils.iter_omr_images(pdf, config())
    assert rendered == []
    name, image = next(images)
    assert name == "scan_p1.png"
    assert rendered == [0]
    expected = image.copy()
    images.close()
    assert doc.is_closed
    np.testing.assert_array_equal(image, expected)


@pytest.mark.parametrize("pages,names", [(None, [1, 2, 3]), (2, [2]), ([3, "1-2", 3, 99], [3, 1, 2])])
def test_pdf_stream_preserves_pixels_and_selected_order(pdf, pages, names):
    loaded = list(ImageUtils.iter_omr_images(pdf, config(pages)))
    assert [name for name, _ in loaded] == [f"scan_p{n}.png" for n in names]
    with fitz.open(pdf) as doc:
        for number, (_, image) in zip(names, loaded):
            pixmap = doc[number - 1].get_pixmap(colorspace=fitz.csGRAY)
            expected = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width)
            np.testing.assert_array_equal(image, expected)
    assert isinstance(ImageUtils.load_omr_image(pdf, config(1)), list)


def test_pdf_auto_dpi_and_invalid_document(pdf, tmp_path):
    assert len(list(ImageUtils.iter_omr_images(pdf, config(dpi="auto")))) == 3
    assert list(ImageUtils.iter_omr_images(tmp_path / "missing.pdf", config())) == []


def test_image_loading_preserves_grayscale_pixels(tmp_path):
    image = np.arange(144, dtype=np.uint8).reshape(12, 12)
    path = tmp_path / "scan.png"
    assert cv2.imwrite(str(path), image)
    loaded = list(ImageUtils.iter_omr_images(path, config()))
    assert loaded[0][0] == "scan.png"
    np.testing.assert_array_equal(loaded[0][1], image)


def test_review_pdf_arrays_own_writable_memory_after_document_closes(pdf):
    import exam_review
    loaded = exam_review._load_page(pdf)
    assert len(loaded) == 3
    for _, image in loaded:
        assert image.flags.owndata and image.flags.writeable
        image[0, 0] = 17
        assert image[0, 0] == 17


def test_debug_images_belong_to_one_instance_and_reset_all_levels():
    first, second = ImageInstanceOps(config(level=3)), ImageInstanceOps(config(level=3))
    first.append_save_img(3, np.ones((8, 8), np.uint8))
    first.ocr_source_image = np.ones((8, 8), np.uint8)
    assert len(first.save_img_list[3]) == 1
    assert dict(second.save_img_list) == {}
    first.save_image_level = 1
    first.reset_all_save_img()
    assert dict(first.save_img_list) == {} and first.ocr_source_image is None


@pytest.mark.parametrize("fail", [False, True])
def test_cli_processes_incrementally_and_releases_on_failure(monkeypatch, fail):
    import src.entry as entry
    ops = ImageInstanceOps(config())
    processed, closed = [], []
    def pages(*_):
        try:
            for index in range(3):
                assert len(processed) == index
                yield f"scan_p{index}.png", np.full((8, 8), index, np.uint8)
        finally:
            closed.append(True)
    def process(_path, name, image, *_):
        ops.append_save_img(1, image)
        ops.ocr_source_image = image
        processed.append(name)
        if fail:
            raise RuntimeError("recognition failed")
    monkeypatch.setattr(ImageUtils, "iter_omr_images", pages)
    monkeypatch.setattr(entry, "_process_single_image", process)
    monkeypatch.setattr(entry, "print_stats", lambda *_: None)
    args = ([Path("scan.pdf")], SimpleNamespace(image_instance_ops=ops), config(), None, None)
    if fail:
        with pytest.raises(RuntimeError, match="recognition failed"):
            entry.process_files(*args)
    else:
        entry.process_files(*args)
        assert len(processed) == 3
    assert closed == [True]
    assert dict(ops.save_img_list) == {} and ops.ocr_source_image is None


def upload(name="card.pdf", data=b"page bytes"):
    return {"name": name, "data": base64.b64encode(data).decode("ascii")}


def test_staging_preserves_bytes_names_and_request(tmp_path):
    import scan_ui
    files = [upload(), upload(data=b"second page")]
    groups = [{"label": "student", "files": files}]
    staged = scan_ui._stage_review_groups(groups, tmp_path / "input")
    paths = staged[0]["files"]
    assert isinstance(paths, scan_ui._StagedReviewFiles)
    assert [path.name for path in paths] == ["card.pdf", "card_2.pdf"]
    assert [path.read_bytes() for path in paths] == [b"page bytes", b"second page"]
    assert groups[0]["files"] == files and "data" in files[0]


def test_invalid_staging_cleans_partial_uploads(tmp_path):
    import scan_ui
    root = tmp_path / "input"
    groups = [{"label": "ok", "files": [upload()]},
              {"label": "invalid", "files": [{"name": "bad.png", "data": "!"}]}]
    with pytest.raises(ValueError):
        scan_ui._stage_review_groups(groups, root)
    assert not root.exists()


@pytest.fixture
def paused_queue(tmp_path, monkeypatch):
    import scan_ui
    class PausedThread:
        def __init__(self, **kwargs):
            self.args, self.target = kwargs["args"], kwargs["target"]
        def start(self):
            pass
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "BATCH_REVIEW_WORKERS", {})
    monkeypatch.setattr(scan_ui, "_review_import", lambda _: ("exam", tmp_path / "exam.json", {"name": "test"}))
    monkeypatch.setattr(scan_ui.TEMPLATE_MANAGER, "get", lambda _: {"id": "template", "name": "test"})
    monkeypatch.setattr(scan_ui, "threading", SimpleNamespace(Thread=PausedThread))
    return scan_ui, PausedThread


def test_queued_jobs_retain_paths_and_worker_cleans_staging(paused_queue, monkeypatch):
    scan_ui, _ = paused_queue
    monkeypatch.setattr(scan_ui, "_create_review_report", lambda *a, **k: {"review_id": "done"})
    result = scan_ui.start_batch_review({"card_groups": [{"label": "a", "files": [upload()]}, {"label": "b", "files": [upload()]}]})
    worker = scan_ui.BATCH_REVIEW_WORKERS[result["batch_id"]]
    groups = worker.args[4]
    assert all(isinstance(group["files"], scan_ui._StagedReviewFiles) for group in groups)
    paths = [path for group in groups for path in group["files"]]
    assert all(isinstance(path, Path) and path.is_file() for path in paths)
    worker.target(*worker.args)
    assert all(not path.exists() for path in paths)
    assert result["batch_id"] not in scan_ui.BATCH_REVIEW_WORKERS


def test_failed_thread_start_releases_staging(paused_queue, monkeypatch):
    scan_ui, thread = paused_queue
    def fail(_):
        raise RuntimeError("thread failed")
    monkeypatch.setattr(thread, "start", fail)
    with pytest.raises(RuntimeError, match="thread failed"):
        scan_ui.start_batch_review({"card_files": [upload()]})
    assert not list(scan_ui.REVIEW_ROOT.glob("batches/*/input"))
    assert dict(scan_ui.BATCH_REVIEW_WORKERS) == {}


def test_file_download_streams_bounded_chunks(tmp_path, monkeypatch):
    import scan_ui
    path = tmp_path / "download.pdf"
    content = b"pdf content" * 100000
    path.write_bytes(content)
    def full_read(*_):
        pytest.fail("whole-file read")
    monkeypatch.setattr(Path, "read_bytes", full_read)
    class Sink:
        def __init__(self):
            self.digest, self.largest = hashlib.sha256(), 0
        def write(self, chunk):
            self.largest = max(self.largest, len(chunk))
            self.digest.update(chunk)
            return len(chunk)
    headers, statuses = {}, []
    sink = Sink()
    handler = SimpleNamespace(wfile=sink, send_response=statuses.append,
                              send_header=lambda k, v: headers.update({k: v}), end_headers=lambda: None)
    scan_ui.ScanUIHandler.send_file(handler, path)
    assert statuses == [200]
    assert headers == {"Content-Type": "application/pdf", "Content-Length": str(len(content)), "Cache-Control": "no-store"}
    assert sink.largest <= 256 * 1024
    assert sink.digest.hexdigest() == hashlib.sha256(content).hexdigest()


def test_extra_review_pages_keep_order_without_unused_feature_alignment(monkeypatch):
    import exam_review
    images = [np.full((8, 8), i, np.uint8) for i in range(4)]
    calls = []
    monkeypatch.setattr(exam_review, "_warp_page", lambda image: image)
    monkeypatch.setattr(exam_review, "_reference_pages", lambda: images[:2])
    def align(image, reference):
        calls.append((int(image[0, 0]), int(reference[0, 0])))
        return image, int(image[0, 0] == reference[0, 0])
    monkeypatch.setattr(exam_review, "_feature_align", align)
    aligned, scores = exam_review._align_and_order_pages(images)
    assert len(calls) == 4
    assert [int(image[0, 0]) for image in aligned] == [0, 1, 2, 3]
    assert scores == [2, 0]
