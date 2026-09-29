"""Keep CSV snapshot paths portable while preserving recognition fields."""
import csv
import io

from src.tests.test_all_samples import extract_sample_outputs, read_file


def test_path_normalization_preserves_answers_and_scores(tmp_path):
    path = tmp_path / "result.csv"
    rows = [["file_id", "input_path", "output_path", "score", "answer"],
            ["a.png", r"samples\a.png", r"outputs\CheckedOMRs\a.png", "5.5", r"\frac{1}{2},x"],
            ["b.png", "samples/b.png", "outputs/CheckedOMRs/b.png", "0", 'a,"b"\nc']]
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, quoting=csv.QUOTE_NONNUMERIC).writerows(rows)
    actual = list(csv.reader(io.StringIO(read_file(path))))
    expected = [row.copy() for row in rows]
    expected[1][1:3] = ["samples/a.png", "outputs/CheckedOMRs/a.png"]
    assert actual == expected


def test_snapshot_keys_use_forward_slashes(tmp_path):
    folder = tmp_path / "Results"
    folder.mkdir()
    path = folder / "Results_08AM.csv"
    path.write_text('"file_id","input_path","output_path"\n', encoding="utf-8")
    assert extract_sample_outputs(tmp_path) == {
        "Results/Results_05AM.csv": '"file_id","input_path","output_path"\n'
    }


def test_empty_snapshot_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    assert read_file(path) == ""
