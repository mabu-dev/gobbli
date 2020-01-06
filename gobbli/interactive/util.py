import csv
import datetime as dt
import itertools
import json
import os
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar

import pandas as pd
import streamlit as st

import gobbli
from gobbli.dataset.base import BaseDataset
from gobbli.io import TaskIO
from gobbli.model.base import BaseModel
from gobbli.model.context import ContainerTaskContext
from gobbli.model.mixin import TrainMixin
from gobbli.util import read_metadata, truncate_text


@st.cache
def get_label_indices(labels: List[str]) -> Dict[str, List[int]]:
    label_indices = defaultdict(list)
    for i, label in enumerate(labels):
        label_indices[label].append(i)
    return label_indices


def _read_delimited(
    data_file: Path, delimiter: str, n_rows: Optional[int] = None
) -> Tuple[List[str], Optional[List[str]]]:
    """
    Read up to n_rows lines from the given delimited text file and return lists
    of the texts and labels.  Texts must be stored in a column named "text", and
    labels (if any) must be stored in a column named "label".

    Args:
      data_file: Data file containing one text per line.
      delimiter: Field delimiter for the data file.
      n_rows: The maximum number of rows to read.

    Returns:
      2-tuple: list of read texts and corresponding list of read labels.
    """
    with open(data_file, "r") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        fieldnames = set(reader.fieldnames)

        if "text" not in fieldnames:
            raise ValueError("Delimited text file doesn't contain a 'text' column.")
        has_labels = "label" in fieldnames

        rows = list(itertools.islice(reader, n_rows))

    texts: List[str] = []
    labels: List[str] = []

    for row in rows:
        texts.append(row["text"])
        if has_labels:
            labels.append(row["label"])

    return texts, labels if has_labels else None


def _read_lines(data_file: Path, n_rows: Optional[int] = None) -> List[str]:
    """
    Read up to n_rows lines from the given text file and return them in a list.

    Args:
      data_file: Data file containing one text per line.
      n_rows: The maximum number of rows to read.

    Returns:
      List of read lines.
    """
    with open(data_file, "r") as f:
        return list(itertools.islice((l.strip() for l in f), n_rows))


def read_data_file(
    data_file: Path, n_rows: Optional[int] = None
) -> Tuple[List[str], Optional[List[str]]]:
    """
    Read data to explore from a file.  Rows may be sampled using the n_rows argument.

    Args:
      data_file: Path to a data file to read.
      n_rows: The maximum number of rows to read.

    Returns:
      2-tuple: list of read texts and a list of read labels (if any)
    """
    extension = data_file.suffix
    if extension == ".tsv":
        texts, labels = _read_delimited(data_file, "\t", n_rows=n_rows)
    elif extension == ".csv":
        texts, labels = _read_delimited(data_file, ",", n_rows=n_rows)
    elif extension == ".txt":
        labels = None
        texts = _read_lines(data_file, n_rows=n_rows)
    else:
        raise ValueError(f"Data file extension '{extension}' is unsupported.")

    return texts, labels


def sample_dataset(
    dataset: BaseDataset, n_rows: Optional[int] = None
) -> Tuple[List[str], List[str]]:
    """
    Sample the given number of rows from the given dataset.

    Args:
      dataset: Loaded dataset to sample from.
      n_rows: Optional number of rows to sample.  If None, return all rows.

    Returns:
      2-tuple: a list of texts and a list of labels.  If n_rows was given, these will
      be no longer than n_rows.
    """
    # Apply limit to the dataset, if any
    if n_rows is None:
        texts = dataset.X_train() + dataset.X_test()
        labels = dataset.y_train() + dataset.y_test()
    else:
        # Try to reach the limit from the train split only first
        train_texts = dataset.X_train()[:n_rows]
        train_labels = dataset.y_train()[:n_rows]

        if len(train_texts) < n_rows:
            # If we need more rows to reach the limit, get them
            # from the test set
            test_limit = n_rows - len(train_texts)
            test_texts = dataset.X_test()[:test_limit]
            test_labels = dataset.y_test()[:test_limit]

            texts = train_texts + test_texts
            labels = train_labels + test_labels
        else:
            # Otherwise, just use the limited train data
            texts = train_texts
            labels = train_labels

    return texts, labels


@st.cache(show_spinner=True)
def read_data_file_cached(
    # Streamlit errors sometimes when hashing Path objects, so use a string.
    # https://github.com/streamlit/streamlit/issues/857
    data_file: str,
    n_rows: Optional[int] = None,
) -> Tuple[List[str], Optional[List[str]]]:
    """
    Streamlit-cached wrapper around :func:`read_data_file` for performance.
    """
    return read_data_file(Path(data_file), n_rows=n_rows)


def load_data(
    data: str, n_rows: Optional[int]
) -> Tuple[List[str], Optional[List[str]]]:
    """
    Load data according to the given 'data' string and row limit.

    Args:
      data: Could be either the name of a gobbli dataset class or a path
      to a data file in a supported format.
      n_rows: Optional limit on number of rows read from the data.

    Returns:
      2-tuple: List of texts and list of labels.
    """
    if os.path.exists(data):
        data_path = Path(data)
        texts, labels = read_data_file_cached(
            str(data_path), n_rows=None if n_rows == -1 else n_rows
        )
    elif data in gobbli.dataset.__all__:
        dataset = getattr(gobbli.dataset, data).load()
        texts, labels = sample_dataset(dataset, None if n_rows == -1 else n_rows)
    else:
        raise ValueError(
            "data argument did not correspond to an existing data file in a "
            "supported format or a built-in gobbli dataset.  Available datasets: "
            f"{gobbli.dataset.__all__}"
        )

    return texts, labels


T = TypeVar("T")


@st.cache
def safe_sample(l: Sequence[T], n: int, seed: Optional[int] = None) -> List[T]:
    if seed is not None:
        random.seed(seed)

    # Prevent an error from trying to sample more than the population
    return list(random.sample(l, min(n, len(l))))


def st_sample_data(
    texts: List[str], labels: Optional[List[str]]
) -> Tuple[List[str], Optional[List[str]]]:
    """
    Generate streamlit sidebar widgets to facilitate sampling a dataset at runtime.

    Args:
      texts: Full list of texts to sample from.
      labels: Full list of labels to sample from.

    Returns:
      2-tuple: the list of sampled texts and list of sampled labels.
    """
    st.sidebar.header("Sample")

    if st.sidebar.button("Randomize Seed"):
        default_seed = random.randint(0, 1000000)
    else:
        default_seed = 1
    sample_seed = st.sidebar.number_input("Sample Seed", value=default_seed)

    sample_size = st.sidebar.slider(
        "Sample Size", min_value=1, max_value=len(texts), value=min(100, len(texts))
    )

    sample_indices = safe_sample(range(len(texts)), sample_size, seed=sample_seed)

    sampled_texts = [texts[i] for i in sample_indices]

    if labels is None:
        sampled_labels = None
    else:
        sampled_labels = [labels[i] for i in sample_indices]

    return sampled_texts, sampled_labels


def st_example_documents(
    texts: List[str], labels: Optional[List[str]], truncate_len: int
):
    """
    Generate streamlit elements showing example documents (and optionally labe
    """
    df = pd.DataFrame({"Document": [truncate_text(t, truncate_len) for t in texts]})
    if labels is not None:
        df["Label"] = labels
    st.table(df)


def format_task(task_dir: Path) -> str:
    """
    Format the given task for a human-readable dropdown.

    Args:
      task_dir: Directory where the task's data is stored.

    Returns:
      String-formatted, human-readable task metadata.
    """
    task_id = task_dir.name
    task_creation_time = dt.datetime.fromtimestamp(task_dir.stat().st_birthtime)
    return f"{task_id[:5]} - Created {task_creation_time.strftime('%Y-%m-%d %H:%M:%S')}"


def st_select_model_checkpoint(
    model_data_path: Path, use_gpu: bool, nvidia_visible_devices: str
):
    """
    Generate widgets allowing for users to select a checkpoint from a given model directory.

    Returns:
      A 3-tuple: the class of model corresponding to the checkpoint, the kwargs to initialize
      the model with, and the metadata for the checkpoint.
    """

    model_info = read_metadata(model_data_path / BaseModel._INFO_FILENAME)
    model_cls_name = model_info["class"]
    if not hasattr(gobbli.model, model_cls_name):
        raise ValueError(f"Unknown model type: {model_cls_name}")
    model_cls = getattr(gobbli.model, model_cls_name)

    model_kwargs = {
        "data_dir": model_data_path,
        "load_existing": True,
        "use_gpu": use_gpu,
        "nvidia_visible_devices": nvidia_visible_devices,
    }
    model = model_cls(**model_kwargs)

    task_metadata = {}
    if isinstance(model, TrainMixin):
        # The model can be trained, so it may have some trained weights
        model_train_dir = model.train_dir()

        # See if any checkpoints are available for the given model
        for task_dir in model_train_dir.iterdir():
            task_context = ContainerTaskContext(task_dir)
            output_dir = task_context.host_output_dir

            if output_dir.exists():
                metadata_path = output_dir / TaskIO._METADATA_FILENAME
                if metadata_path.exists():
                    with open(metadata_path, "r") as f:
                        metadata = json.load(f)

                        if "checkpoint" in metadata:
                            task_formatted = format_task(task_dir)
                            task_metadata[task_formatted] = metadata

    if len(task_metadata) == 0:
        st.error("No trained checkpoints found for the given model.")
        return

    st.sidebar.header("Model")
    model_checkpoint = st.sidebar.selectbox("Checkpoint", list(task_metadata.keys()))
    return model_cls, model_kwargs, task_metadata[model_checkpoint]
