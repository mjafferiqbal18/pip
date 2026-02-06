# PyPI Dependency Analysis — Database Collections Reference

This document summarizes the MongoDB collections used/created in this project, their **schemas**, **how they were built** (source + script), and **what they represent** in the pipeline (global graph, time-aware pruning, subgraphs, and adjacency structures).

> Notes
> - Names like `pypi_dump.<collection>` indicate the database and collection.
> - Field types are described informally (e.g., `int`, `str`, `array[int]`, `Binary`).
> - “node_id” refers to your **integer node id** for `(canonical_name, version)` pairs.
> - “name_id” refers to your **integer id** for a canonicalized package name.

---

## 1) Source collections

### 1.1 `pypi_dump.distribution_metadata`
**Purpose:** Raw PyPI release metadata per uploaded artifact; source-of-truth for package `name`, `version`, `upload_time`, dependency metadata, etc. The names are not cannonicalized. requires_dist contains a list of strings representing requirements. 

```json
{
  "_id": {
    "$oid": "68ff9415b27d8945e521dcb3"
  },
  "metadata_version": "2.1",
  "name": "pandas",
  "version": "2.3.3",
  "summary": "Powerful data structures for data analysis, time series, and statistics",
  "license": "BSD 3-Clause License\n         \n         Copyright (c) 2008-2011, AQR Capital Management, LLC, Lambda Foundry, Inc. and PyData Development Team\n         All rights reserved.\n         \n         Copyright (c) 2011-2023, Open source contributors.\n         \n         Redistribution and use in source and binary forms, with or without\n         modification, are permitted provided that the following conditions are met:\n         \n         * Redistributions of source code must retain the above copyright notice, this\n           list of conditions and the following disclaimer.\n         \n         * Redistributions in binary form must reproduce the above copyright notice,\n           this list of conditions and the following disclaimer in the documentation\n           and/or other materials provided with the distribution.\n         \n         * Neither the name of the copyright holder nor the names of its\n           contributors may be used to endorse or promote products derived from\n           this software without specific prior written permission.\n         \n         THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS \"AS IS\"\n         AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE\n         IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE\n         DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE\n         FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL\n         DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR\n         SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER\n         CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,\n         OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE\n         OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n         ",
  "classifiers": [
    "Development Status :: 5 - Production/Stable",
    "Environment :: Console",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: BSD License",
    "Operating System :: OS Independent",
    "Programming Language :: Cython",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Topic :: Scientific/Engineering"
  ],
  "platform": [],
  "requires_python": ">=3.9",
  "requires": [],
  "provides": [],
  "obsoletes": [],
  "requires_dist": [
    "numpy>=1.22.4; python_version < \"3.11\"",
    "numpy>=1.23.2; python_version == \"3.11\"",
    "numpy>=1.26.0; python_version >= \"3.12\"",
    "python-dateutil>=2.8.2",
    "pytz>=2020.1",
    "tzdata>=2022.7",
    "hypothesis>=6.46.1; extra == \"test\"",
    "pytest>=7.3.2; extra == \"test\"",
    "pytest-xdist>=2.2.0; extra == \"test\"",
    "pyarrow>=10.0.1; extra == \"pyarrow\"",
    "bottleneck>=1.3.6; extra == \"performance\"",
    "numba>=0.56.4; extra == \"performance\"",
    "numexpr>=2.8.4; extra == \"performance\"",
    "scipy>=1.10.0; extra == \"computation\"",
    "xarray>=2022.12.0; extra == \"computation\"",
    "fsspec>=2022.11.0; extra == \"fss\"",
    "s3fs>=2022.11.0; extra == \"aws\"",
    "gcsfs>=2022.11.0; extra == \"gcp\"",
    "pandas-gbq>=0.19.0; extra == \"gcp\"",
    "odfpy>=1.4.1; extra == \"excel\"",
    "openpyxl>=3.1.0; extra == \"excel\"",
    "python-calamine>=0.1.7; extra == \"excel\"",
    "pyxlsb>=1.0.10; extra == \"excel\"",
    "xlrd>=2.0.1; extra == \"excel\"",
    "xlsxwriter>=3.0.5; extra == \"excel\"",
    "pyarrow>=10.0.1; extra == \"parquet\"",
    "pyarrow>=10.0.1; extra == \"feather\"",
    "tables>=3.8.0; extra == \"hdf5\"",
    "pyreadstat>=1.2.0; extra == \"spss\"",
    "SQLAlchemy>=2.0.0; extra == \"postgresql\"",
    "psycopg2>=2.9.6; extra == \"postgresql\"",
    "adbc-driver-postgresql>=0.8.0; extra == \"postgresql\"",
    "SQLAlchemy>=2.0.0; extra == \"mysql\"",
    "pymysql>=1.0.2; extra == \"mysql\"",
    "SQLAlchemy>=2.0.0; extra == \"sql-other\"",
    "adbc-driver-postgresql>=0.8.0; extra == \"sql-other\"",
    "adbc-driver-sqlite>=0.8.0; extra == \"sql-other\"",
    "beautifulsoup4>=4.11.2; extra == \"html\"",
    "html5lib>=1.1; extra == \"html\"",
    "lxml>=4.9.2; extra == \"html\"",
    "lxml>=4.9.2; extra == \"xml\"",
    "matplotlib>=3.6.3; extra == \"plot\"",
    "jinja2>=3.1.2; extra == \"output-formatting\"",
    "tabulate>=0.9.0; extra == \"output-formatting\"",
    "PyQt5>=5.15.9; extra == \"clipboard\"",
    "qtpy>=2.3.0; extra == \"clipboard\"",
    "zstandard>=0.19.0; extra == \"compression\"",
    "dataframe-api-compat>=0.1.7; extra == \"consortium-standard\"",
    "adbc-driver-postgresql>=0.8.0; extra == \"all\"",
    "adbc-driver-sqlite>=0.8.0; extra == \"all\"",
    "beautifulsoup4>=4.11.2; extra == \"all\"",
    "bottleneck>=1.3.6; extra == \"all\"",
    "dataframe-api-compat>=0.1.7; extra == \"all\"",
    "fastparquet>=2022.12.0; extra == \"all\"",
    "fsspec>=2022.11.0; extra == \"all\"",
    "gcsfs>=2022.11.0; extra == \"all\"",
    "html5lib>=1.1; extra == \"all\"",
    "hypothesis>=6.46.1; extra == \"all\"",
    "jinja2>=3.1.2; extra == \"all\"",
    "lxml>=4.9.2; extra == \"all\"",
    "matplotlib>=3.6.3; extra == \"all\"",
    "numba>=0.56.4; extra == \"all\"",
    "numexpr>=2.8.4; extra == \"all\"",
    "odfpy>=1.4.1; extra == \"all\"",
    "openpyxl>=3.1.0; extra == \"all\"",
    "pandas-gbq>=0.19.0; extra == \"all\"",
    "psycopg2>=2.9.6; extra == \"all\"",
    "pyarrow>=10.0.1; extra == \"all\"",
    "pymysql>=1.0.2; extra == \"all\"",
    "PyQt5>=5.15.9; extra == \"all\"",
    "pyreadstat>=1.2.0; extra == \"all\"",
    "pytest>=7.3.2; extra == \"all\"",
    "pytest-xdist>=2.2.0; extra == \"all\"",
    "python-calamine>=0.1.7; extra == \"all\"",
    "pyxlsb>=1.0.10; extra == \"all\"",
    "qtpy>=2.3.0; extra == \"all\"",
    "scipy>=1.10.0; extra == \"all\"",
    "s3fs>=2022.11.0; extra == \"all\"",
    "SQLAlchemy>=2.0.0; extra == \"all\"",
    "tables>=3.8.0; extra == \"all\"",
    "tabulate>=0.9.0; extra == \"all\"",
    "xarray>=2022.12.0; extra == \"all\"",
    "xlrd>=2.0.1; extra == \"all\"",
    "xlsxwriter>=3.0.5; extra == \"all\"",
    "zstandard>=0.19.0; extra == \"all\""
  ],
  "provides_dist": [],
  "obsoletes_dist": [],
  "requires_external": [],
  "project_urls": [
    "homepage, https://pandas.pydata.org",
    "documentation, https://pandas.pydata.org/docs/",
    "repository, https://github.com/pandas-dev/pandas"
  ],
  "upload_time": "2025-09-29T23:34:51.853367+00:00",
  "filename": "pandas-2.3.3.tar.gz",
  "size": "4495223",
  "python_version": "source",
  "packagetype": "sdist"
}
```

**Important notes:**
- There may be **multiple docs** per `(name, version, packagetype=sdist)`.
- `upload_time` strings may contain fractional seconds with **5 digits** (e.g., `.11519`) which is not always accepted by `datetime.fromisoformat()` unless you normalize to 6 digits.

**Used for:**
- Base source of information about packages. We downloaded this dataset and parse it (instead of repeatedly querying PyPI for a package's information)
- Building node ids / name ids.
- Building global graph edges by parsing `requires_dist`.
- Building requires-python constraints and timestamps for node ids.

---

### 1.2 `pypi_dump.vuln_per_version`
**Purpose:** Vulnerability mapping per package/version (names used to seed vulnerable roots / subgraph creation).

**Key fields:** 
- `name` (package name, may be non-canonical)
- version 
- vulnerabilities in that version, also contains the fixed version of the package

```json
{
  "_id": {
    "$oid": "6958de107793343a1a4b0c82"
  },
  "name": "bleach",
  "version": "2.1",
  "vulnerabilities": [
    {
      "vuln_id": "PYSEC-2018-51",
      "fixed_version": "2.1.3"
    },
    {
      "vuln_id": "PYSEC-2021-865",
      "fixed_version": "3.3.0"
    },
    {
      "vuln_id": "PYSEC-2020-28",
      "fixed_version": "3.1.2"
    },
    {
      "vuln_id": "PYSEC-2020-340",
      "fixed_version": "3.1.4"
    },
    {
      "vuln_id": "PYSEC-2020-27",
      "fixed_version": "3.1.1"
    }
  ],
  "fixed_version": "3.3.0"
}
```

**Used for:**
- Identifying root vulnerable packages and their versions

---

## 2) Global identifiers and attributes

### 2.1 `pypi_dump.global_graph_node_ids`
**Purpose:** Assign a **stable integer node id** to each `(canonical_name, version)` pair.

**Schema:**
```json
{
  "_id": {
    "$oid": "6964e828154387269c4d6686"
  },
  "version": "2.3.3",
  "name": "pandas",
  "id": 4553200
}
```

**Built from:**
- Source: `pypi_dump.distribution_metadata` filtered to `packagetype == "sdist"`, with canonicalized `name`, stringified `version`.
- Script: `build_global_graph_node_ids.py`.

**Represents:**
- The universe of “installable versions” as nodes. Each node (package,version) is assigned a unique integer id.

**Used for:**
- Consistent ids across all downstream collections (global graph edges, timestamps, requires_python masks, adjacency chunks, subgraphs).

---

### 2.2 `pypi_dump.global_graph_name_ids`
**Purpose:** Assign a **stable integer name_id** to each canonical package name.

**Schema (recommended):**
```json
{
  "_id": {
    "$oid": "6980bf58fbc5874390b0fa81"
  },
  "name": "numpy",
  "id": 426011
}
```

**Built from:**
- Source: `pypi_dump.global_graph_node_ids` (collect unique `name`).
- Script: `build_global_graph_name_ids.py`.

**Used for:**
- Compact representation of dependency package names in forward adjacency (`dep_name_id`).

---

### 2.3 `pypi_dump.global_graph_requires_python_with_timestamps`
**Purpose:** Store **python compatibility mask** and **first upload time** per `node_id`.


**Key fields:** 
- `_id` represents node_id
- `py_mask` represents the set of python versions (Major.Minor, e.g. 3.9) that this node is compatible with. Represented as a 32 bit int. Built using PY_CANDIDATES (shown below). Bits for the allowed version are set to 1. Built from requires_python strings (see below). 
- `first_upload_time` represents the first upload time of the package on PyPI.

**Schema:**
```json
{
  "_id": 4553200,
  "py_mask": 7937,
  "first_upload_time": {
    "$date": "2025-09-29T23:34:51.853Z"
  }
}
```

```python
PY_CANDIDATES = [
    '3.9','3.8','3.7','3.6','3.5','3.4','3.3','3.2',
    '3.14','3.13','3.12','3.11','3.10','3.1','3.0',
    '2.7','2.6','2.5','2.4','2.3','2.2','2.1','2.0',
    '1.6','1.5','1.4'
]
```

**Built from:**
- Source: `pypi_dump.distribution_metadata` filtered to `packagetype == "sdist"`. (you may have multiple such docs for a single package,version)
- Join key: canonicalized `(name, version)` → `node_id` via `global_graph_node_ids`.
- Requires-python policy:
  - Union masks of **non-empty** `requires_python` strings (all specified requires_python requirements across sdist docs of a package name,version).
  - If none non-empty for `(name, version)`, mask = **all-ones**.
  - The py_mask is built from the PY_CANDIDATES in the python snippet above, and represented as a 32 bit int.
- Timestamp policy:
  - Take **earliest upload_time** observed for the `(name, version)` group.
  - strings may contain fractional seconds with **5 digits** (e.g., `.11519`) which is not always accepted by `datetime.fromisoformat()` unless you normalize to 6 digits.

**Script:** `build_global_graph_requires_python_with_timestamps.py`. and later patched padding issue to rectify null values using `patch_null_first_upload_time.py`

**Used for:**
- Time-aware candidate pruning (`<= t_cutoff`).
- CSP solver constraints via python-mask intersection.

---

## 3) Global graph edges (version-level dependency edges)

### 3.1 `pypi_dump.global_graph`
**Purpose:** Version-level dependency edges derived from parsed `requires_dist` (we skip extra requirements).  

**Schema (observed):**
```json
{
  "_id": {
    "$oid": "6931352d352bde2c14326e84"
  },
  "src_name": "pandas",
  "src_version": "2.3.3",
  "src_id": {
    "$oid": "68ff9415b27d8945e521dcb3"
  },
  "src_requires_python": ">=3.9",
  "src_upload_time": {
    "$date": "2025-09-29T23:34:51.853Z"
  },
  "dst_name": "python-dateutil",
  "dst_version": "2.8.2",
  "dst_id": {
    "$oid": "68ff936c62e5e2c50d707bed"
  },
  "dst_requires_python": "!=3.0.*,!=3.1.*,!=3.2.*,>=2.7",
  "dst_upload_time": {
    "$date": "2021-07-14T08:19:19.783Z"
  }
}
```

**Built from:** `distribution_metadata.requires_dist` parsing. A requires_dist string is parsed, and matched to concrete qualifying versions for the node. For example, (pandas,2.3.3) requires_dist had "python-dateutil>=2.8.2" in its requires_dist; an edge is drawn from src=(pandas,2.3.3) to dst=(python-dateutil,2.8.2) where dst is one qualifying version given the requires_dist string (there can be many, and are resolved into separate edges in the graph). So instead of keeping constraints, we resolve those to concrete edges between nodes.
**Script:** `build_globalGraph_batched_latest.py`.

**Important note:** you later remap `(name, version)` to your integer `node_id` for downstream work.

---

## 4) Reverse adjacency (backward reachability)

### 4.1 `pypi_dump.global_graph_edges_id_part`
**Purpose:** Staging edges with integer ids + partition key.

**Schema (observed):**
```json
{
  "_id": "ObjectId",
  "part": 340,
  "dst_id": 5560660,
  "src_id": 4
}
```

**Built from:** `global_graph` by mapping `(src_name,src_version)` and `(dst_name,dst_version)` to int node_ids via `global_graph_node_ids`.  
**Script:** `build_global_reverse_adjacency.py`.

**Represents:** compact edge list partitioned for scalable aggregation.

---

### 4.2 `pypi_dump.global_graph_reverse_adjacency`
**Purpose:** Chunked reverse adjacency list: for each `dst_id`, store src dependents.

**Schema (observed):**
```json
{
  "_id": { "dst_id": 5120, "chunk": 0 },
  "dst_id": 5120,
  "chunk": 0,
  "src_ids": [4973432, 4973433]
}
```

**Built from:** `global_graph_edges_id_part` grouped by `dst_id` with chunking.  
**Script:** `build_global_reverse_adjacency.py`.

**Used for:** backward DFS/BFS from vulnerable roots to build vulnerable subgraphs.

---

## 5) Vulnerable subgraphs (per root version)

### 5.1 `subgraphs.<root>_subgraph__meta`
**Purpose:** Metadata for a vulnerable subgraph family (many root versions packed). root_versions is an array representing the versions (I believe they are sorted in ascending order), and root_ids represents the corresponding node_ids for those versions. nBits represents the number of vulnerable root versions for the package, and thus the corresponding number of bits. For eg. 83 represents 83 vuln root versions, and we have 83 bits (0 to 82) representing the bit membership for an edge (more in section 5.2).

**Schema (observed):**
```json
{
  "_id": "ObjectId",
  "pkg": "urllib3",
  "root_versions": ["..."],
  "root_ids": [541...],
  "nbits": 83,
  "out_coll": "urllib3_subgraph",
  "stats": { "nodes_per_bit": [], "edges_per_bit": [], "max_depth_per_bit": [] }
}
```

**Used for:** selecting a particular root version’s bit index and its `root_id`.

---

### 5.2 `subgraphs.<root>_subgraph`
**Purpose:** Edge list for the subgraph family; membership in root-version subgraphs via bitset. To find edges in the latest versions of a vulnerable root with nBits=83, one would filter edges from this subgraph where the 82th roots_bit is set.

**Schema (observed):**
```json
{
  "_id": {
    "$oid": "69675f327cff38ae69398c7e"
  },
  "src_id": 2248381,
  "dst_id": 1183744,
  "roots_bits": {
    "$binary": {
      "base64": "/////////////wc=",
      "subType": "00"
    }
  }
}
```

**Interpretation:** if bit `i` is set, edge belongs to root version `root_versions[i]`.  
**Used for:** streaming edges for one bit to collect nodes and analyze exposure.

---

## 6) Forward adjacency for time-aware CSP (headers + chunks)

### 6.1 `pypi_dump.global_graph_edges_ids_srcpart` *(phase 1 staging)*
**Purpose:** Compact staging of forward edges partitioned by `src_id`. (Similar to 4.1 `pypi_dump.global_graph_edges_id_part` but now partitioned by src_id for easy grouping in phase 2)

**Schema (intended/used):**
```json
{
  "_id": {
    "$oid": "6980d1a24b192b57a230d6d5"
  },
  "part": 260,
  "src_id": 991492,
  "dst_id": 2373141
}
```

**Built from:** `global_graph` + `global_graph_node_ids` + `global_graph_name_ids`.  
**Script:** `build_global_forward_part.py`.

---
### 6.2 `pypi_dump.global_graph_adj_deps`
**Purpose:** Intermediate “dependency list” per `src_id` that tells you **which dependency package names** a source node depends on, without carrying all candidate versions inline. This is used so the solver can quickly get the set of dependency package groups for a node (and then consult headers/chunks to enumerate time-filtered version candidates). 

- `_id` represents the src node id
- `deps` represents the name_id of the packages (so package names, not their versions) src depends on

**Schema (typical/expected):**
```json
{
  "_id": 991492,
  "deps": [
    225641,
    226133,
    540251
  ]
}
```
**Built from:** Source staging: `pypi_dump.global_graph_edges_ids_srcpart` which contains (src_id, dst_id) edges.

**Used for:** Given a src_id, quickly get the list of dependency package groups to resolve 

---
### 6.3 `pypi_dump.global_graph_adj_headers`
**Purpose:** For each `(src_id, dep_name_id)`, store per-chunk time bounds and counts. 

**Schema (confirmed):**
```json
{
  "_id": { "src_id": 991492, "dep_name_id": 225641 },
  "src_id": 991492,
  "dep_name_id": 225641,
  "mi": [1271442577],
  "ma": [1755651801],
  "n": [70],
  "total": 70
}
```

- `mi[i]`: min `first_upload_time` in chunk `i`
- `ma[i]`: max `first_upload_time` in chunk `i`
- `n[i]`: number of `dst_ids` in chunk `i`
- `total`: sum of `n`

**Built from:** grouped `(src_id, dep_name_id)` edges, using `first_upload_time` from `global_graph_requires_python_with_timestamps` to sort and chunk.  
**Scripts:** phase 2/3 builders.

---

### 6.4 `pypi_dump.global_graph_adj_chunks`
**Purpose:** Store actual candidate dst node ids per chunk, sorted by upload time.

**Schema (confirmed):**
```json
{
  "_id": { "src_id": 2048, "dep_name_id": 100127, "chunk": 0 },
  "src_id": 2048,
  "dep_name_id": 100127,
  "chunk": 0,
  "dst_ids": [1243153]
}
```

**Built from:** same grouped forward edges after sorting by time and chunking.  
**Used for:** fast enumeration of candidates `<= t_cutoff`.

---

## 7) Phase 4 exposure outputs

### 7.1 `*_nodes_exposure.csv`
**Purpose:** Per-node exposure result for one subgraph bit/root version.

**Typical columns (intended):**
- `node_id`
- `node_time` (epoch)
- `t_cutoff = max(node_time, root_time)`
- `exposed` (True/False)
- `depth` (if exposed)
- optional `reason` counters (debug)

**Built by:** `phase4_exposure_nodes_*.py`.

---

## 8) Access patterns and invariants (quick)

### Invariants
- Names in id collections are canonicalized consistently.
- `global_graph_node_ids` is the authoritative mapping for `(canon_name, version) → node_id`.
- `global_graph_requires_python_with_timestamps` provides:
  - `first_upload_time` for time cutoff checks (no null times)
  - `py_mask` for python compatibility intersection
- In `global_graph_adj_chunks`, `dst_ids` are sorted by `first_upload_time` ascending (to allow for binary search for ersions <= t>).

### Typical queries
- headers: `find_one({src_id, dep_name_id})` → arrays `mi/ma/n`
- chunks:  `find_one({src_id, dep_name_id, chunk})` → `dst_ids`

---

## 9) Collection dependency graph (build lineage)

- `distribution_metadata`
  - → `global_graph_node_ids` (node ids)
  - → `global_graph` (requires_dist parsing)
  - → `global_graph_requires_python_with_timestamps` (python masks + earliest upload time)

- `global_graph_node_ids`
  - → `global_graph_name_ids` (unique names)
  - used to remap `global_graph` edges to int ids

- `global_graph` + id maps
  - → `global_graph_edges_id_part`
  - → `global_graph_reverse_adjacency`
  - → `subgraphs.<root>_subgraph` + `<root>_subgraph__meta`

- `global_graph` + id maps + timestamps/name_ids
  - → `global_graph_edges_ids_srcpart`
  - → `global_graph_adj_deps` + `global_graph_adj_chunks` + `global_graph_adj_headers`

- `subgraphs.*` + `global_graph_adj_*` + timestamps/masks
  - → `phase4 exposure CSV outputs`

### 10) Most relevant collections and their summaries:
From these, all collections (apart from the global_graph_adj_chunks) can be read into memory as maps in Python
One can use an LRU cache (e.g. size=200k entries) for `global_graph_adj_chunks` data as well

- `global_graph_node_ids`: maps a node_id to package (name,version)
- `global_graph_name_ids`: maps a name_id to package name
- `global_graph_requires_python_with_timestamps`: maps a node_id to first_upload_time and py_mask (allowed Python versions)
- `global_graph_adj_deps`: maps a node_id to name_ids of dependencies it requires (direct dependencies)
- `global_graph_adj_headers`: maps a node_id, dependency name_id to chunks(sorted by time), min and max times per chunk
- `global_graph_adj_chunks`: maps a node_id, dependency name_id, chunk to the set of qualifying versions (node_ids of concrete qualifying versions of direct dependencies)
