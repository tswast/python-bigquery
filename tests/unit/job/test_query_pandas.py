# Copyright 2015 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import concurrent.futures
import copy
import json

import mock
import pytest

try:
    import pandas
except (ImportError, AttributeError):  # pragma: NO COVER
    pandas = None
try:
    import pyarrow
except (ImportError, AttributeError):  # pragma: NO COVER
    pyarrow = None
try:
    from google.cloud import bigquery_storage
except (ImportError, AttributeError):  # pragma: NO COVER
    bigquery_storage = None
try:
    from tqdm import tqdm
except (ImportError, AttributeError):  # pragma: NO COVER
    tqdm = None

from .helpers import _make_client
from .helpers import _make_connection
from .helpers import _make_job_resource


@pytest.fixture
def table_read_options_kwarg():
    # Create a BigQuery Storage table read options object with pyarrow compression
    # enabled if a recent-enough version of google-cloud-bigquery-storage dependency is
    # installed to support the compression.
    if not hasattr(bigquery_storage, "ArrowSerializationOptions"):
        return {}

    read_options = bigquery_storage.ReadSession.TableReadOptions(
        arrow_serialization_options=bigquery_storage.ArrowSerializationOptions(
            buffer_compression=bigquery_storage.ArrowSerializationOptions.CompressionCodec.LZ4_FRAME
        )
    )
    return {"read_options": read_options}


@pytest.mark.parametrize(
    "query,expected",
    (
        (None, False),
        ("", False),
        ("select name, age from table", False),
        ("select name, age from table LIMIT 10;", False),
        ("select name, age from table order by other_column;", True),
        ("Select name, age From table Order By other_column", True),
        ("SELECT name, age FROM table ORDER BY other_column;", True),
        ("select name, age from table order\nby other_column", True),
        ("Select name, age From table Order\nBy other_column;", True),
        ("SELECT name, age FROM table ORDER\nBY other_column", True),
        ("SelecT name, age froM table OrdeR \n\t BY other_column;", True),
    ),
)
def test__contains_order_by(query, expected):
    from google.cloud.bigquery import job as mut

    if expected:
        assert mut._contains_order_by(query)
    else:
        assert not mut._contains_order_by(query)


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(
    bigquery_storage is None, reason="Requires `google-cloud-bigquery-storage`"
)
@pytest.mark.parametrize(
    "query",
    (
        "select name, age from table order by other_column;",
        "Select name, age From table Order By other_column;",
        "SELECT name, age FROM table ORDER BY other_column;",
        "select name, age from table order\nby other_column;",
        "Select name, age From table Order\nBy other_column;",
        "SELECT name, age FROM table ORDER\nBY other_column;",
        "SelecT name, age froM table OrdeR \n\t BY other_column;",
    ),
)
def test_to_dataframe_bqstorage_preserve_order(query, table_read_options_kwarg):
    from google.cloud.bigquery.job import QueryJob as target_class

    job_resource = _make_job_resource(
        project_id="test-project", job_type="query", ended=True
    )
    job_resource["configuration"]["query"]["query"] = query
    job_resource["status"] = {"state": "DONE"}
    get_query_results_resource = {
        "jobComplete": True,
        "jobReference": {"projectId": "test-project", "jobId": "test-job"},
        "schema": {
            "fields": [
                {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                {"name": "age", "type": "INTEGER", "mode": "NULLABLE"},
            ]
        },
        "totalRows": "4",
    }
    connection = _make_connection(get_query_results_resource, job_resource)
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(job_resource, client)
    bqstorage_client = mock.create_autospec(bigquery_storage.BigQueryReadClient)
    session = bigquery_storage.types.ReadSession()
    session.avro_schema.schema = json.dumps(
        {
            "type": "record",
            "name": "__root__",
            "fields": [
                {"name": "name", "type": ["null", "string"]},
                {"name": "age", "type": ["null", "long"]},
            ],
        }
    )
    bqstorage_client.create_read_session.return_value = session

    job.to_dataframe(bqstorage_client=bqstorage_client)

    destination_table = "projects/{projectId}/datasets/{datasetId}/tables/{tableId}".format(
        **job_resource["configuration"]["query"]["destinationTable"]
    )
    expected_session = bigquery_storage.ReadSession(
        table=destination_table,
        data_format=bigquery_storage.DataFormat.ARROW,
        **table_read_options_kwarg,
    )
    bqstorage_client.create_read_session.assert_called_once_with(
        parent="projects/test-project",
        read_session=expected_session,
        max_stream_count=1,  # Use a single stream to preserve row order.
    )


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
def test_to_arrow():
    from google.cloud.bigquery.job import QueryJob as target_class

    begun_resource = _make_job_resource(job_type="query")
    query_resource = {
        "jobComplete": True,
        "jobReference": begun_resource["jobReference"],
        "totalRows": "4",
        "schema": {
            "fields": [
                {
                    "name": "spouse_1",
                    "type": "RECORD",
                    "fields": [
                        {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "age", "type": "INTEGER", "mode": "NULLABLE"},
                    ],
                },
                {
                    "name": "spouse_2",
                    "type": "RECORD",
                    "fields": [
                        {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "age", "type": "INTEGER", "mode": "NULLABLE"},
                    ],
                },
            ]
        },
    }
    tabledata_resource = {
        "rows": [
            {
                "f": [
                    {"v": {"f": [{"v": "Phred Phlyntstone"}, {"v": "32"}]}},
                    {"v": {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]}},
                ]
            },
            {
                "f": [
                    {"v": {"f": [{"v": "Bhettye Rhubble"}, {"v": "27"}]}},
                    {"v": {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]}},
                ]
            },
        ]
    }
    done_resource = copy.deepcopy(begun_resource)
    done_resource["status"] = {"state": "DONE"}
    connection = _make_connection(
        begun_resource, query_resource, done_resource, tabledata_resource
    )
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    tbl = job.to_arrow(create_bqstorage_client=False)

    assert isinstance(tbl, pyarrow.Table)
    assert tbl.num_rows == 2

    # Check the schema.
    assert tbl.schema[0].name == "spouse_1"
    assert tbl.schema[0].type[0].name == "name"
    assert tbl.schema[0].type[1].name == "age"
    assert pyarrow.types.is_struct(tbl.schema[0].type)
    assert pyarrow.types.is_string(tbl.schema[0].type[0].type)
    assert pyarrow.types.is_int64(tbl.schema[0].type[1].type)
    assert tbl.schema[1].name == "spouse_2"
    assert tbl.schema[1].type[0].name == "name"
    assert tbl.schema[1].type[1].name == "age"
    assert pyarrow.types.is_struct(tbl.schema[1].type)
    assert pyarrow.types.is_string(tbl.schema[1].type[0].type)
    assert pyarrow.types.is_int64(tbl.schema[1].type[1].type)

    # Check the data.
    tbl_data = tbl.to_pydict()
    spouse_1 = tbl_data["spouse_1"]
    assert spouse_1 == [
        {"name": "Phred Phlyntstone", "age": 32},
        {"name": "Bhettye Rhubble", "age": 27},
    ]
    spouse_2 = tbl_data["spouse_2"]
    assert spouse_2 == [
        {"name": "Wylma Phlyntstone", "age": 29},
        {"name": "Bharney Rhubble", "age": 33},
    ]


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
def test_to_arrow_max_results_no_progress_bar():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField

    connection = _make_connection({})
    client = _make_client(connection=connection)
    begun_resource = _make_job_resource(job_type="query")
    job = target_class.from_api_repr(begun_resource, client)

    schema = [
        SchemaField("name", "STRING", mode="REQUIRED"),
        SchemaField("age", "INTEGER", mode="REQUIRED"),
    ]
    rows = [
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
    ]
    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result", return_value=row_iterator,
    )
    with result_patch as result_patch_tqdm:
        tbl = job.to_arrow(create_bqstorage_client=False, max_results=123)

    result_patch_tqdm.assert_called_once_with(max_results=123)

    assert isinstance(tbl, pyarrow.Table)
    assert tbl.num_rows == 2


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_arrow_w_tqdm_w_query_plan():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField
    from google.cloud.bigquery._tqdm_helpers import _PROGRESS_BAR_UPDATE_INTERVAL

    begun_resource = _make_job_resource(job_type="query")
    rows = [
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
    ]

    schema = [
        SchemaField("name", "STRING", mode="REQUIRED"),
        SchemaField("age", "INTEGER", mode="REQUIRED"),
    ]
    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    job._properties["statistics"] = {
        "query": {
            "queryPlan": [
                {"name": "S00: Input", "id": "0", "status": "COMPLETE"},
                {"name": "S01: Output", "id": "1", "status": "COMPLETE"},
            ]
        },
    }
    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[
            concurrent.futures.TimeoutError,
            concurrent.futures.TimeoutError,
            row_iterator,
        ],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        tbl = job.to_arrow(progress_bar_type="tqdm", create_bqstorage_client=False)

    assert result_patch_tqdm.call_count == 3
    assert isinstance(tbl, pyarrow.Table)
    assert tbl.num_rows == 2
    result_patch_tqdm.assert_called_with(
        timeout=_PROGRESS_BAR_UPDATE_INTERVAL, max_results=None
    )


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_arrow_w_tqdm_w_pending_status():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField
    from google.cloud.bigquery._tqdm_helpers import _PROGRESS_BAR_UPDATE_INTERVAL

    begun_resource = _make_job_resource(job_type="query")
    rows = [
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
    ]

    schema = [
        SchemaField("name", "STRING", mode="REQUIRED"),
        SchemaField("age", "INTEGER", mode="REQUIRED"),
    ]
    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    job._properties["statistics"] = {
        "query": {
            "queryPlan": [
                {"name": "S00: Input", "id": "0", "status": "PENDING"},
                {"name": "S00: Input", "id": "1", "status": "COMPLETE"},
            ]
        },
    }
    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[concurrent.futures.TimeoutError, row_iterator],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        tbl = job.to_arrow(progress_bar_type="tqdm", create_bqstorage_client=False)

    assert result_patch_tqdm.call_count == 2
    assert isinstance(tbl, pyarrow.Table)
    assert tbl.num_rows == 2
    result_patch_tqdm.assert_called_with(
        timeout=_PROGRESS_BAR_UPDATE_INTERVAL, max_results=None
    )


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_arrow_w_tqdm_wo_query_plan():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField

    begun_resource = _make_job_resource(job_type="query")
    rows = [
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
    ]

    schema = [
        SchemaField("name", "STRING", mode="REQUIRED"),
        SchemaField("age", "INTEGER", mode="REQUIRED"),
    ]
    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[concurrent.futures.TimeoutError, row_iterator],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        tbl = job.to_arrow(progress_bar_type="tqdm", create_bqstorage_client=False)

    assert result_patch_tqdm.call_count == 2
    assert isinstance(tbl, pyarrow.Table)
    assert tbl.num_rows == 2
    result_patch_tqdm.assert_called()


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
def test_to_dataframe():
    from google.cloud.bigquery.job import QueryJob as target_class

    begun_resource = _make_job_resource(job_type="query")
    query_resource = {
        "jobComplete": True,
        "jobReference": begun_resource["jobReference"],
        "totalRows": "4",
        "schema": {
            "fields": [
                {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                {"name": "age", "type": "INTEGER", "mode": "NULLABLE"},
            ]
        },
    }
    tabledata_resource = {
        "rows": [
            {"f": [{"v": "Phred Phlyntstone"}, {"v": "32"}]},
            {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
            {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
            {"f": [{"v": "Bhettye Rhubble"}, {"v": "27"}]},
        ]
    }
    done_resource = copy.deepcopy(begun_resource)
    done_resource["status"] = {"state": "DONE"}
    connection = _make_connection(
        begun_resource, query_resource, done_resource, tabledata_resource
    )
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    df = job.to_dataframe(create_bqstorage_client=False)

    assert isinstance(df, pandas.DataFrame)
    assert len(df) == 4  # verify the number of rows
    assert list(df) == ["name", "age"]  # verify the column names


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
def test_to_dataframe_ddl_query():
    from google.cloud.bigquery.job import QueryJob as target_class

    # Destination table may have no schema for some DDL and DML queries.
    resource = _make_job_resource(job_type="query", ended=True)
    query_resource = {
        "jobComplete": True,
        "jobReference": resource["jobReference"],
        "schema": {"fields": []},
    }
    connection = _make_connection(query_resource)
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(resource, client)

    df = job.to_dataframe()

    assert len(df) == 0


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(
    bigquery_storage is None, reason="Requires `google-cloud-bigquery-storage`"
)
def test_to_dataframe_bqstorage(table_read_options_kwarg):
    from google.cloud.bigquery.job import QueryJob as target_class

    resource = _make_job_resource(job_type="query", ended=True)
    query_resource = {
        "jobComplete": True,
        "jobReference": resource["jobReference"],
        "totalRows": "4",
        "schema": {
            "fields": [
                {"name": "name", "type": "STRING", "mode": "NULLABLE"},
                {"name": "age", "type": "INTEGER", "mode": "NULLABLE"},
            ]
        },
    }
    connection = _make_connection(query_resource)
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(resource, client)
    bqstorage_client = mock.create_autospec(bigquery_storage.BigQueryReadClient)
    session = bigquery_storage.types.ReadSession()
    session.avro_schema.schema = json.dumps(
        {
            "type": "record",
            "name": "__root__",
            "fields": [
                {"name": "name", "type": ["null", "string"]},
                {"name": "age", "type": ["null", "long"]},
            ],
        }
    )
    bqstorage_client.create_read_session.return_value = session

    job.to_dataframe(bqstorage_client=bqstorage_client)

    destination_table = "projects/{projectId}/datasets/{datasetId}/tables/{tableId}".format(
        **resource["configuration"]["query"]["destinationTable"]
    )
    expected_session = bigquery_storage.ReadSession(
        table=destination_table,
        data_format=bigquery_storage.DataFormat.ARROW,
        **table_read_options_kwarg,
    )
    bqstorage_client.create_read_session.assert_called_once_with(
        parent=f"projects/{client.project}",
        read_session=expected_session,
        max_stream_count=0,  # Use default number of streams for best performance.
    )


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(
    bigquery_storage is None, reason="Requires `google-cloud-bigquery-storage`"
)
def test_to_dataframe_bqstorage_no_pyarrow_compression():
    from google.cloud.bigquery.job import QueryJob as target_class

    resource = _make_job_resource(job_type="query", ended=True)
    query_resource = {
        "jobComplete": True,
        "jobReference": resource["jobReference"],
        "totalRows": "4",
        "schema": {"fields": [{"name": "name", "type": "STRING", "mode": "NULLABLE"}]},
    }
    connection = _make_connection(query_resource)
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(resource, client)
    bqstorage_client = mock.create_autospec(bigquery_storage.BigQueryReadClient)
    session = bigquery_storage.types.ReadSession()
    session.avro_schema.schema = json.dumps(
        {
            "type": "record",
            "name": "__root__",
            "fields": [{"name": "name", "type": ["null", "string"]}],
        }
    )
    bqstorage_client.create_read_session.return_value = session

    with mock.patch(
        "google.cloud.bigquery._pandas_helpers._ARROW_COMPRESSION_SUPPORT", new=False
    ):
        job.to_dataframe(bqstorage_client=bqstorage_client)

    destination_table = "projects/{projectId}/datasets/{datasetId}/tables/{tableId}".format(
        **resource["configuration"]["query"]["destinationTable"]
    )
    expected_session = bigquery_storage.ReadSession(
        table=destination_table, data_format=bigquery_storage.DataFormat.ARROW,
    )
    bqstorage_client.create_read_session.assert_called_once_with(
        parent=f"projects/{client.project}",
        read_session=expected_session,
        max_stream_count=0,
    )


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
def test_to_dataframe_column_dtypes():
    from google.cloud.bigquery.job import QueryJob as target_class

    begun_resource = _make_job_resource(job_type="query")
    query_resource = {
        "jobComplete": True,
        "jobReference": begun_resource["jobReference"],
        "totalRows": "4",
        "schema": {
            "fields": [
                {"name": "start_timestamp", "type": "TIMESTAMP"},
                {"name": "seconds", "type": "INT64"},
                {"name": "miles", "type": "FLOAT64"},
                {"name": "km", "type": "FLOAT64"},
                {"name": "payment_type", "type": "STRING"},
                {"name": "complete", "type": "BOOL"},
                {"name": "date", "type": "DATE"},
            ]
        },
    }
    row_data = [
        [
            "1433836800000000",
            "420",
            "1.1",
            "1.77",
            "Cto_dataframeash",
            "true",
            "1999-12-01",
        ],
        ["1387811700000000", "2580", "17.7", "28.5", "Cash", "false", "1953-06-14"],
        ["1385565300000000", "2280", "4.4", "7.1", "Credit", "true", "1981-11-04"],
    ]
    rows = [{"f": [{"v": field} for field in row]} for row in row_data]
    query_resource["rows"] = rows
    done_resource = copy.deepcopy(begun_resource)
    done_resource["status"] = {"state": "DONE"}
    connection = _make_connection(
        begun_resource, query_resource, done_resource, query_resource
    )
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    df = job.to_dataframe(dtypes={"km": "float16"}, create_bqstorage_client=False)

    assert isinstance(df, pandas.DataFrame)
    assert len(df) == 3  # verify the number of rows
    exp_columns = [field["name"] for field in query_resource["schema"]["fields"]]
    assert list(df) == exp_columns  # verify the column names

    assert df.start_timestamp.dtype.name == "datetime64[ns, UTC]"
    assert df.seconds.dtype.name == "int64"
    assert df.miles.dtype.name == "float64"
    assert df.km.dtype.name == "float16"
    assert df.payment_type.dtype.name == "object"
    assert df.complete.dtype.name == "bool"
    assert df.date.dtype.name == "object"


@pytest.mark.skipif(pyarrow is None, reason="Requires `pyarrow`")
@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
def test_to_dataframe_column_date_dtypes():
    from google.cloud.bigquery.job import QueryJob as target_class

    begun_resource = _make_job_resource(job_type="query")
    query_resource = {
        "jobComplete": True,
        "jobReference": begun_resource["jobReference"],
        "totalRows": "1",
        "schema": {"fields": [{"name": "date", "type": "DATE"}]},
    }
    row_data = [
        ["1999-12-01"],
    ]
    rows = [{"f": [{"v": field} for field in row]} for row in row_data]
    query_resource["rows"] = rows
    done_resource = copy.deepcopy(begun_resource)
    done_resource["status"] = {"state": "DONE"}
    connection = _make_connection(
        begun_resource, query_resource, done_resource, query_resource
    )
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)
    df = job.to_dataframe(date_as_object=False, create_bqstorage_client=False)

    assert isinstance(df, pandas.DataFrame)
    assert len(df) == 1  # verify the number of rows
    exp_columns = [field["name"] for field in query_resource["schema"]["fields"]]
    assert list(df) == exp_columns  # verify the column names
    assert df.date.dtype.name == "datetime64[ns]"


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
@mock.patch("tqdm.tqdm")
def test_to_dataframe_with_progress_bar(tqdm_mock):
    from google.cloud.bigquery.job import QueryJob as target_class

    begun_resource = _make_job_resource(job_type="query")
    query_resource = {
        "jobComplete": True,
        "jobReference": begun_resource["jobReference"],
        "totalRows": "4",
        "schema": {"fields": [{"name": "name", "type": "STRING", "mode": "NULLABLE"}]},
    }
    done_resource = copy.deepcopy(begun_resource)
    done_resource["status"] = {"state": "DONE"}
    connection = _make_connection(
        begun_resource, query_resource, done_resource, query_resource, query_resource,
    )
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    job.to_dataframe(progress_bar_type=None, create_bqstorage_client=False)
    tqdm_mock.assert_not_called()

    job.to_dataframe(progress_bar_type="tqdm", create_bqstorage_client=False)
    tqdm_mock.assert_called()


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_dataframe_w_tqdm_pending():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField
    from google.cloud.bigquery._tqdm_helpers import _PROGRESS_BAR_UPDATE_INTERVAL

    begun_resource = _make_job_resource(job_type="query")
    schema = [
        SchemaField("name", "STRING", mode="NULLABLE"),
        SchemaField("age", "INTEGER", mode="NULLABLE"),
    ]
    rows = [
        {"f": [{"v": "Phred Phlyntstone"}, {"v": "32"}]},
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
        {"f": [{"v": "Bhettye Rhubble"}, {"v": "27"}]},
    ]

    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    job._properties["statistics"] = {
        "query": {
            "queryPlan": [
                {"name": "S00: Input", "id": "0", "status": "PRNDING"},
                {"name": "S01: Output", "id": "1", "status": "COMPLETE"},
            ]
        },
    }
    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[concurrent.futures.TimeoutError, row_iterator],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        df = job.to_dataframe(progress_bar_type="tqdm", create_bqstorage_client=False)

    assert result_patch_tqdm.call_count == 2
    assert isinstance(df, pandas.DataFrame)
    assert len(df) == 4  # verify the number of rows
    assert list(df) == ["name", "age"]  # verify the column names
    result_patch_tqdm.assert_called_with(
        timeout=_PROGRESS_BAR_UPDATE_INTERVAL, max_results=None
    )


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_dataframe_w_tqdm():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField
    from google.cloud.bigquery._tqdm_helpers import _PROGRESS_BAR_UPDATE_INTERVAL

    begun_resource = _make_job_resource(job_type="query")
    schema = [
        SchemaField("name", "STRING", mode="NULLABLE"),
        SchemaField("age", "INTEGER", mode="NULLABLE"),
    ]
    rows = [
        {"f": [{"v": "Phred Phlyntstone"}, {"v": "32"}]},
        {"f": [{"v": "Bharney Rhubble"}, {"v": "33"}]},
        {"f": [{"v": "Wylma Phlyntstone"}, {"v": "29"}]},
        {"f": [{"v": "Bhettye Rhubble"}, {"v": "27"}]},
    ]

    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    job._properties["statistics"] = {
        "query": {
            "queryPlan": [
                {"name": "S00: Input", "id": "0", "status": "COMPLETE"},
                {"name": "S01: Output", "id": "1", "status": "COMPLETE"},
            ]
        },
    }
    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[
            concurrent.futures.TimeoutError,
            concurrent.futures.TimeoutError,
            row_iterator,
        ],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        df = job.to_dataframe(progress_bar_type="tqdm", create_bqstorage_client=False)

    assert result_patch_tqdm.call_count == 3
    assert isinstance(df, pandas.DataFrame)
    assert len(df) == 4  # verify the number of rows
    assert list(df), ["name", "age"]  # verify the column names
    result_patch_tqdm.assert_called_with(
        timeout=_PROGRESS_BAR_UPDATE_INTERVAL, max_results=None
    )


@pytest.mark.skipif(pandas is None, reason="Requires `pandas`")
@pytest.mark.skipif(tqdm is None, reason="Requires `tqdm`")
def test_to_dataframe_w_tqdm_max_results():
    from google.cloud.bigquery import table
    from google.cloud.bigquery.job import QueryJob as target_class
    from google.cloud.bigquery.schema import SchemaField
    from google.cloud.bigquery._tqdm_helpers import _PROGRESS_BAR_UPDATE_INTERVAL

    begun_resource = _make_job_resource(job_type="query")
    schema = [
        SchemaField("name", "STRING", mode="NULLABLE"),
        SchemaField("age", "INTEGER", mode="NULLABLE"),
    ]
    rows = [{"f": [{"v": "Phred Phlyntstone"}, {"v": "32"}]}]

    connection = _make_connection({})
    client = _make_client(connection=connection)
    job = target_class.from_api_repr(begun_resource, client)

    path = "/foo"
    api_request = mock.Mock(return_value={"rows": rows})
    row_iterator = table.RowIterator(client, api_request, path, schema)

    job._properties["statistics"] = {
        "query": {
            "queryPlan": [
                {"name": "S00: Input", "id": "0", "status": "COMPLETE"},
                {"name": "S01: Output", "id": "1", "status": "COMPLETE"},
            ]
        },
    }
    reload_patch = mock.patch(
        "google.cloud.bigquery.job._AsyncJob.reload", autospec=True
    )
    result_patch = mock.patch(
        "google.cloud.bigquery.job.QueryJob.result",
        side_effect=[concurrent.futures.TimeoutError, row_iterator],
    )

    with result_patch as result_patch_tqdm, reload_patch:
        job.to_dataframe(
            progress_bar_type="tqdm", create_bqstorage_client=False, max_results=3
        )

    assert result_patch_tqdm.call_count == 2
    result_patch_tqdm.assert_called_with(
        timeout=_PROGRESS_BAR_UPDATE_INTERVAL, max_results=3
    )
