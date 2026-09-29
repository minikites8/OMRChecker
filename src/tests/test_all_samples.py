import csv
import io
import os
import re
import shutil
from glob import glob
from pathlib import Path

from src.tests.utils import run_entry_point, setup_mocker_patches


def read_file(path):
    """Canonicalize CSV path columns for the shared cross-platform snapshots."""
    with open(path, encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    if not rows:
        return ""
    path_columns = [
        index for index, name in enumerate(rows[0])
        if name in {"input_path", "output_path"}
    ]
    for row in rows[1:]:
        for index in path_columns:
            if index < len(row):
                row[index] = row[index].replace("\\", "/")
    output = io.StringIO()
    csv.writer(output, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n").writerows(rows)
    return output.getvalue()


def run_sample(mocker, sample_path):
    setup_mocker_patches(mocker)

    input_path = os.path.join("samples", sample_path)
    output_dir = os.path.join("outputs", sample_path)
    if os.path.exists(output_dir):
        print(
            f"Warning: output directory already exists: {output_dir}. This may affect the test execution."
        )

    run_entry_point(input_path, output_dir)

    sample_outputs = extract_sample_outputs(output_dir)

    print(f"Note: removing output directory: {output_dir}")
    shutil.rmtree(output_dir)

    return sample_outputs


EXT = "*.csv"


def extract_sample_outputs(output_dir):
    sample_outputs = {}
    for _dir, _subdir, _files in os.walk(output_dir):
        for file in glob(os.path.join(_dir, EXT)):
            relative_path = Path(os.path.relpath(file, output_dir))
            # Snapshot data is independent of the local hour in the output filename.
            if relative_path.parent.name == "Results" and re.fullmatch(
                r"Results_(?:0[1-9]|1[0-2])(?:AM|PM)\.csv", relative_path.name
            ):
                relative_path = relative_path.with_name("Results_05AM.csv")
            sample_outputs[relative_path.as_posix()] = read_file(file)
    return sample_outputs


def test_run_answer_key_using_csv(mocker, snapshot):
    sample_outputs = run_sample(mocker, "answer-key/using-csv")
    assert snapshot == sample_outputs


def test_run_answer_key_weighted_answers(mocker, snapshot):
    sample_outputs = run_sample(mocker, "answer-key/weighted-answers")
    assert snapshot == sample_outputs


def test_run_sample1(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample1")
    assert snapshot == sample_outputs


def test_run_sample2(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample2")
    assert snapshot == sample_outputs


def test_run_sample3(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample3")
    assert snapshot == sample_outputs


def test_run_sample4(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample4")
    assert snapshot == sample_outputs


def test_run_sample5(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample5")
    assert snapshot == sample_outputs


def test_run_sample6(mocker, snapshot):
    sample_outputs = run_sample(mocker, "sample6")
    assert snapshot == sample_outputs


def test_run_community_Antibodyy(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/Antibodyy")
    assert snapshot == sample_outputs


def test_run_community_ibrahimkilic(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/ibrahimkilic")
    assert snapshot == sample_outputs


def test_run_community_Sandeep_1507(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/Sandeep-1507")
    assert snapshot == sample_outputs


def test_run_community_Shamanth(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/Shamanth")
    assert snapshot == sample_outputs


def test_run_community_UmarFarootAPS(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/UmarFarootAPS")
    assert snapshot == sample_outputs


def test_run_community_UPSC_mock(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/UPSC-mock")
    assert snapshot == sample_outputs
def test_run_community_celin_mampilly(mocker, snapshot):
    sample_outputs = run_sample(mocker, "community/celin-mampilly")
    assert snapshot == sample_outputs