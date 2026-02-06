# Dependency Resolution Data Structures

This document provides a fine-grained, detailed specification of all data structures used in pip's dependency resolution and backtracking system.

## Table of Contents

1. [Data Transformation Pipeline](#data-transformation-pipeline)
2. [Core Data Structure Schemas](#core-data-structure-schemas)
3. [Python Version Tracking](#python-version-tracking)
4. [Backtracking Data Structures](#backtracking-data-structures)
5. [Final Dependency Tree Representation](#final-dependency-tree-representation)

---

## Data Transformation Pipeline

### Stage 1: Command Line Input → InstallRequirement

**Input**: `str` (e.g., `"pandas"`)

**Location**: `src/pip/_internal/req/constructors.py`

**Transformation**:
```python
# install_req_from_line("pandas")
InstallRequirement(
    req=Requirement("pandas"),  # packaging.requirements.Requirement
    comes_from=None,
    editable=False,
    link=None,
    constraint=False,
    user_supplied=True,
    ...
)
```

**Schema**: `InstallRequirement` (see below)

---

### Stage 2: InstallRequirement → Resolver Requirement

**Location**: `src/pip/_internal/resolution/resolvelib/factory.py:collect_root_requirements()`

**Transformation**:
```python
# For regular requirements (not constraints)
SpecifierRequirement(ireq)  # or ExplicitRequirement, RequiresPythonRequirement
```

**Schema**: `Requirement` base class (see below)

---

### Stage 3: Requirement → Candidate Discovery

**Location**: `src/pip/_internal/resolution/resolvelib/provider.py:find_matches()`

**Process**:
1. Query PyPI simple API: `https://pypi.org/simple/{package}/`
2. Parse HTML to extract distribution links
3. Filter by version specifiers, Python version, platform tags
4. Return `LinkCandidate` objects

**Transformation**:
```python
Requirement → Iterable[Candidate]
```

---

### Stage 4: Candidate → Dependencies (Requirement Discovery)

**Location**: `src/pip/_internal/resolution/resolvelib/candidates.py:iter_dependencies()`

**Process**:
1. Download/prepare distribution metadata
2. Parse `Requires-Dist` fields from METADATA/PKG-INFO
3. Convert each dependency string to `Requirement` objects

**Transformation**:
```python
Candidate → Iterable[Requirement]
```

---

### Stage 5: Resolution State → Final Result

**Location**: `src/pip/_vendor/resolvelib/resolvers/resolution.py:_build_result()`

**Transformation**:
```python
State → Result(
    mapping=dict[KT, CT],      # Final pinned candidates
    graph=DirectedGraph,      # Dependency graph
    criteria=dict[KT, Criterion]  # Resolution criteria
)
```

---

## Core Data Structure Schemas

### InstallRequirement

**Location**: `src/pip/_internal/req/req_install.py:62`

**Purpose**: Represents a package requirement before resolution. Used throughout pip's installation pipeline.

**Schema**:
```python
class InstallRequirement:
    # Core requirement data
    req: Requirement | None                    # packaging.requirements.Requirement
    comes_from: str | InstallRequirement | None
    constraint: bool                           # True if from constraint file
    editable: bool                            # True if -e/--editable
    user_supplied: bool                       # True if from CLI/requirements file
    
    # Link/URL information
    link: Link | None                         # Download URL
    original_link: Link | None
    cached_wheel_source_link: Link | None
    
    # Package metadata
    name: str | None                          # Canonicalized package name
    extras: set[str]                          # Optional extras (e.g., ["dev", "test"])
    markers: Marker | None                    # Environment markers
    
    # Version constraints
    specifier: SpecifierSet                    # Version specifier (e.g., ">=1.0,<2.0")
    
    # Installation state
    satisfied_by: BaseDistribution | None     # If already installed
    should_reinstall: bool
    prepared: bool
    install_succeeded: bool | None
    
    # Build/installation paths
    source_dir: str | None
    local_file_path: str | None
    metadata_directory: str | None
    
    # Build system
    pyproject_requires: list[str] | None
    pep517_backend: BuildBackendHookCaller | None
    
    # Hash verification
    hash_options: dict[str, list[str]]
    
    # Configuration
    isolated: bool
    config_settings: dict[str, str | list[str]] | None
```

**Key Methods**:
- `specifier`: Returns `SpecifierSet` from `req.specifier`
- `hashes(trust_internet: bool)`: Returns `Hashes` object
- `match_markers()`: Checks if environment markers are satisfied

---

### Constraint

**Location**: `src/pip/_internal/resolution/resolvelib/base.py:25`

**Purpose**: Represents user-specified constraints (from `-c` files) that limit candidate selection.

**Schema**:
```python
@dataclass(frozen=True)
class Constraint:
    specifier: SpecifierSet              # Version constraints (e.g., ">=1.0,<2.0")
    hashes: Hashes                       # Hash constraints for verification
    links: frozenset[Link]               # URL constraints (specific distributions)
```

**Key Methods**:
- `is_satisfied_by(candidate: Candidate) -> bool`: Checks if candidate satisfies constraint
- `__and__(other: InstallRequirement) -> Constraint`: Merges constraints

**Example**:
```python
Constraint(
    specifier=SpecifierSet(">=1.0,<2.0"),
    hashes=Hashes({...}),
    links=frozenset()
)
```

---

### Requirement (Resolver-Level)

**Location**: `src/pip/_internal/resolution/resolvelib/base.py:63`

**Purpose**: Abstract base class for requirements in the resolver. Represents a dependency constraint.

**Schema**:
```python
class Requirement:
    @property
    def project_name(self) -> NormalizedName: ...
    @property
    def name(self) -> str: ...                    # May include extras: "pkg[extra]"
    
    def is_satisfied_by(candidate: Candidate) -> bool: ...
    def get_candidate_lookup() -> CandidateLookup: ...
    def format_for_error() -> str: ...
```

**Concrete Implementations**:

#### SpecifierRequirement

**Location**: `src/pip/_internal/resolution/resolvelib/requirements.py:52`

**Schema**:
```python
class SpecifierRequirement(Requirement):
    _ireq: InstallRequirement
    _extras: frozenset[NormalizedName]
    
    # Properties
    project_name: NormalizedName          # e.g., "pandas"
    name: str                             # e.g., "pandas" or "pandas[dev]"
    
    # Methods
    is_satisfied_by(candidate) -> bool    # Checks version specifier
    get_candidate_lookup() -> (None, InstallRequirement)
```

**Example**:
```python
SpecifierRequirement(
    InstallRequirement(req=Requirement("numpy>=1.22.4"))
)
# name: "numpy"
# project_name: "numpy"
```

#### ExplicitRequirement

**Location**: `src/pip/_internal/resolution/resolvelib/requirements.py:14`

**Purpose**: Represents a requirement for a specific candidate (e.g., from URL or already pinned).

**Schema**:
```python
class ExplicitRequirement(Requirement):
    candidate: Candidate                  # The exact candidate required
    
    # Methods
    is_satisfied_by(candidate) -> bool    # True only if candidate == self.candidate
    get_candidate_lookup() -> (Candidate, None)
```

#### RequiresPythonRequirement

**Location**: `src/pip/_internal/resolution/resolvelib/requirements.py:158`

**Purpose**: Represents Python version requirement from `Requires-Python` metadata.

**Schema**:
```python
class RequiresPythonRequirement(Requirement):
    specifier: SpecifierSet               # e.g., ">=3.9"
    _candidate: Candidate                 # RequiresPythonCandidate
    
    # Properties
    project_name: NormalizedName           # "<Python from Requires-Python>"
    name: str                             # "<Python from Requires-Python>"
    
    # Methods
    is_satisfied_by(candidate) -> bool    # Checks Python version
```

---

### Candidate

**Location**: `src/pip/_internal/resolution/resolvelib/base.py:99`

**Purpose**: Represents a specific package version that can be installed.

**Schema**:
```python
class Candidate:
    @property
    def project_name(self) -> NormalizedName: ...  # Base package name
    @property
    def name(self) -> str: ...                     # May include extras
    @property
    def version(self) -> Version: ...              # packaging.version.Version
    @property
    def is_installed(self) -> bool: ...
    @property
    def is_editable(self) -> bool: ...
    @property
    def source_link(self) -> Link | None: ...
    
    def iter_dependencies(with_requires: bool) -> Iterable[Requirement | None]: ...
    def get_install_requirement() -> InstallRequirement | None: ...
    def format_for_error() -> str: ...
```

**Concrete Implementations**:

#### LinkCandidate

**Location**: `src/pip/_internal/resolution/resolvelib/candidates.py:275`

**Purpose**: Represents a candidate from a remote or local distribution file.

**Schema**:
```python
class LinkCandidate(_InstallRequirementBackedCandidate):
    is_editable = False
    is_installed = False
    
    # Internal
    _link: Link                            # Download link
    _source_link: Link                     # Original source (before cache)
    _ireq: InstallRequirement
    _factory: Factory
    dist: BaseDistribution                 # Parsed metadata
    
    # Properties (from Candidate)
    project_name: NormalizedName
    name: str
    version: Version
    source_link: Link | None
```

**Example**:
```python
LinkCandidate(
    link=Link("https://files.pythonhosted.org/.../pandas-2.3.3.whl"),
    template=InstallRequirement(...),
    factory=Factory(...)
)
# project_name: "pandas"
# name: "pandas"
# version: Version("2.3.3")
```

#### AlreadyInstalledCandidate

**Location**: `src/pip/_internal/resolution/resolvelib/candidates.py:356`

**Purpose**: Represents a package already installed in the environment.

**Schema**:
```python
class AlreadyInstalledCandidate(Candidate):
    is_installed = True
    source_link = None
    
    dist: BaseDistribution                 # Installed distribution
    _ireq: InstallRequirement
    _factory: Factory
```

#### RequiresPythonCandidate

**Location**: `src/pip/_internal/resolution/resolvelib/candidates.py:551`

**Purpose**: Represents the Python interpreter version.

**Schema**:
```python
class RequiresPythonCandidate(Candidate):
    is_installed = False
    source_link = None
    
    _version: Version                      # Python version (e.g., "3.10.0")
    
    # Properties
    project_name: NormalizedName           # "<Python from Requires-Python>"
    name: str                              # "<Python from Requires-Python>"
    version: Version                       # Current Python version
```

**Example**:
```python
RequiresPythonCandidate(py_version_info=(3, 10, 0))
# name: "<Python from Requires-Python>"
# version: Version("3.10.0")
```

#### ExtrasCandidate

**Location**: `src/pip/_internal/resolution/resolvelib/candidates.py:427`

**Purpose**: Wraps a base candidate with extras (e.g., `pandas[dev]`).

**Schema**:
```python
class ExtrasCandidate(Candidate):
    base: BaseCandidate                    # Underlying candidate
    extras: frozenset[NormalizedName]      # e.g., {"dev", "test"}
    
    # Properties
    project_name: NormalizedName           # Same as base
    name: str                              # "pkg[extra1,extra2]"
    version: Version                       # Same as base
```

---

### State

**Location**: `src/pip/_vendor/resolvelib/structs.py:31`

**Purpose**: Represents the current resolution state during backtracking.

**Schema**:
```python
State = NamedTuple(
    mapping: dict[KT, CT],                 # Pinned candidates: {identifier: candidate}
    criteria: dict[KT, Criterion[RT, CT]], # Requirements per identifier
    backtrack_causes: list[RequirementInformation[RT, CT]]  # Conflict causes
)
```

**Type Parameters**:
- `KT`: Identifier type (typically `str`)
- `RT`: Requirement type (e.g., `Requirement`)
- `CT`: Candidate type (e.g., `Candidate`)

**Example**:
```python
State(
    mapping={
        "pandas": LinkCandidate(...),
        "numpy": LinkCandidate(...),
        "<Python from Requires-Python>": RequiresPythonCandidate(...)
    },
    criteria={
        "pandas": Criterion(...),
        "numpy": Criterion(...),
        ...
    },
    backtrack_causes=[]  # Empty if no conflicts
)
```

---

### Criterion

**Location**: `src/pip/_vendor/resolvelib/resolvers/criterion.py:8`

**Purpose**: Represents all requirements and possible candidates for a single identifier.

**Schema**:
```python
class Criterion(Generic[RT, CT]):
    candidates: Iterable[CT]               # Possible candidates to try
    information: Collection[RequirementInformation[RT, CT]]  # Requirements + parents
    incompatibilities: Collection[CT]     # Known incompatible candidates
```

**RequirementInformation**:
```python
RequirementInformation = NamedTuple(
    requirement: RT,                        # The requirement
    parent: CT | None                      # Candidate that requires this (None = root)
)
```

**Example**:
```python
Criterion(
    candidates=[LinkCandidate("numpy-2.2.6"), LinkCandidate("numpy-2.1.0"), ...],
    information=[
        RequirementInformation(
            requirement=SpecifierRequirement("numpy>=1.22.4"),
            parent=LinkCandidate("pandas-2.3.3")
        )
    ],
    incompatibilities=[]  # Candidates that failed
)
```

---

### Result

**Location**: `src/pip/_vendor/resolvelib/resolvers/abstract.py:13`

**Purpose**: Final resolution result containing the dependency graph.

**Schema**:
```python
class Result(NamedTuple, Generic[RT, CT, KT]):
    mapping: dict[KT, CT]                  # Final pinned candidates
    graph: DirectedGraph[KT | None]        # Dependency graph
    criteria: dict[KT, Criterion[RT, CT]]  # Final criteria
```

**DirectedGraph**:
```python
class DirectedGraph(Generic[KT]):
    _vertices: set[KT]                     # All nodes
    _forwards: dict[KT, set[KT]]           # Forward edges: {parent: {children}}
    _backwards: dict[KT, set[KT]]          # Backward edges: {child: {parents}}
```

**Example**:
```python
Result(
    mapping={
        "pandas": LinkCandidate("pandas-2.3.3"),
        "numpy": LinkCandidate("numpy-2.2.6"),
        ...
    },
    graph=DirectedGraph(
        _vertices={"pandas", "numpy", None, ...},
        _forwards={
            None: {"pandas"},              # Root → pandas
            "pandas": {"numpy"},           # pandas → numpy
            ...
        },
        _backwards={
            "pandas": {None},
            "numpy": {"pandas"},
            ...
        }
    ),
    criteria={...}
)
```

---

## Python Version Tracking

### How Python Version is Maintained

**Location**: `src/pip/_internal/resolution/resolvelib/factory.py:105`

**Initialization**:
```python
self._python_candidate = RequiresPythonCandidate(py_version_info)
# py_version_info: tuple[int, ...] | None  # e.g., (3, 10, 0)
```

**Python Version Requirements**:

1. **From Package Metadata**: When a candidate's metadata contains `Requires-Python`, a `RequiresPythonRequirement` is created.

2. **Special Identifier**: Python uses a special identifier:
   ```python
   REQUIRES_PYTHON_IDENTIFIER = "<Python from Requires-Python>"
   ```

3. **Priority in Resolution**: Python requirements are checked first via `narrow_requirement_selection()`:
   ```python
   if identifier == REQUIRES_PYTHON_IDENTIFIER:
       return [identifier]  # Check Python first
   ```

**Data Structures**:

```python
# Python candidate (singleton per resolution)
RequiresPythonCandidate(
    _version=Version("3.10.0")  # Current Python version
)

# Python requirements (one per package that specifies Requires-Python)
RequiresPythonRequirement(
    specifier=SpecifierSet(">=3.9"),
    _candidate=RequiresPythonCandidate(...)
)

# In State.mapping
{
    "<Python from Requires-Python>": RequiresPythonCandidate(...),
    ...
}
```

**Merging Python Requirements**:

Multiple packages may specify different `Requires-Python` constraints. The resolver:
1. Creates a `RequiresPythonRequirement` for each constraint
2. All point to the same `RequiresPythonCandidate`
3. The resolver ensures the Python version satisfies **all** constraints (intersection)

---

## Backtracking Data Structures

### State Stack

**Location**: `src/pip/_vendor/resolvelib/resolvers/resolution.py:82`

**Purpose**: Maintains history of resolution states for backtracking.

**Schema**:
```python
self._states: list[State[RT, CT, KT]] = []
```

**Structure**:
```python
[
    State(...),  # Root state (empty)
    State(...),  # After round 0
    State(...),  # After round 1
    ...
    State(...),  # Current state
]
```

**Operations**:
- `_push_new_state()`: Creates new state from current, appends to stack
- `_backjump()`: Pops states until finding conflict cause

---

### Backtrack Causes

**Location**: `src/pip/_vendor/resolvelib/structs.py:39`

**Purpose**: Tracks which requirements caused conflicts to guide backtracking.

**Schema**:
```python
backtrack_causes: list[RequirementInformation[RT, CT]]
```

**Example**:
```python
backtrack_causes=[
    RequirementInformation(
        requirement=SpecifierRequirement("numpy>=2.0"),
        parent=LinkCandidate("pandas-2.3.3")
    ),
    RequirementInformation(
        requirement=SpecifierRequirement("numpy<2.0"),
        parent=LinkCandidate("scipy-1.11.0")
    )
]
```

**Usage**:
1. **Extracted from conflicts**: When `_attempt_to_pin_criterion()` fails, causes are extracted
2. **Stored in state**: `state.backtrack_causes[:] = causes`
3. **Used for prioritization**: `narrow_requirement_selection()` prioritizes identifiers in `backtrack_causes`

---

### Incompatibilities

**Location**: `src/pip/_vendor/resolvelib/resolvers/criterion.py:32`

**Purpose**: Tracks candidates known to be incompatible (failed in previous attempts).

**Schema**:
```python
incompatibilities: Collection[CT]  # List of failed candidates
```

**Example**:
```python
Criterion(
    candidates=[...],
    information=[...],
    incompatibilities=[
        LinkCandidate("numpy-2.2.6"),  # Failed due to conflict
        LinkCandidate("numpy-2.1.0"),  # Also failed
    ]
)
```

**Usage**:
- When a candidate fails, it's added to `incompatibilities`
- `find_matches()` excludes incompatible candidates
- During backtracking, incompatibilities are preserved and applied to new states

---

### Backjumping Data Flow

**Location**: `src/pip/_vendor/resolvelib/resolvers/resolution.py:305`

**Process**:

1. **Conflict Detection**:
   ```python
   failure_criterion = self._attempt_to_pin_criterion(name)
   if failure_criterion:
       causes = self._extract_causes(failure_criterion)
   ```

2. **State Popping**:
   ```python
   del self._states[-1]  # Remove failed state
   broken_state = self._states.pop()  # Get previous state
   name, candidate = broken_state.mapping.popitem()  # Get last pin
   ```

3. **Incompatibility Collection**:
   ```python
   incompatibilities_from_broken = [
       (k, list(v.incompatibilities)) 
       for k, v in broken_state.criteria.items()
   ]
   incompatibilities_from_broken.append((name, [candidate]))  # Mark failed candidate
   ```

4. **State Recreation**:
   ```python
   self._push_new_state()  # Create new state from earlier one
   self._patch_criteria(incompatibilities_from_broken)  # Apply incompatibilities
   ```

**Data Transformations**:

```
State Stack Before:
  [State0, State1, State2, State3]  # State3 failed

Extract Causes:
  causes = [RequirementInformation(...)]

Pop States:
  del State3
  broken_state = State2
  name, candidate = ("numpy", LinkCandidate("numpy-2.2.6"))

Collect Incompatibilities:
  incompatibilities_from_broken = [
      ("numpy", [LinkCandidate("numpy-2.2.6")]),
      ("pandas", [LinkCandidate("pandas-2.3.3")]),  # If it also failed
  ]

Create New State:
  State4 = State2.copy()  # Based on State2
  State4.criteria["numpy"].incompatibilities.append(LinkCandidate("numpy-2.2.6"))

State Stack After:
  [State0, State1, State2, State4]  # State4 ready to retry
```

---

## Final Dependency Tree Representation

### Result Structure

**Location**: `src/pip/_vendor/resolvelib/resolvers/abstract.py:13`

**Schema**:
```python
Result(
    mapping: dict[str, Candidate],         # Final pinned versions
    graph: DirectedGraph[str | None],      # Dependency relationships
    criteria: dict[str, Criterion]        # Final resolution criteria
)
```

### DirectedGraph Structure

**Location**: `src/pip/_vendor/resolvelib/structs.py:45`

**Schema**:
```python
DirectedGraph:
    _vertices: set[str | None]             # All package identifiers + None (root)
    _forwards: dict[str | None, set[str]]  # Parent → children
    _backwards: dict[str, set[str | None]] # Child → parents
```

**Example**:
```python
graph = DirectedGraph(
    _vertices={None, "pandas", "numpy", "pytz", "tzdata", "python-dateutil", "six"},
    _forwards={
        None: {"pandas"},                  # Root → pandas
        "pandas": {"numpy", "pytz", "tzdata", "python-dateutil"},
        "python-dateutil": {"six"},
    },
    _backwards={
        "pandas": {None},
        "numpy": {"pandas"},
        "pytz": {"pandas"},
        "tzdata": {"pandas"},
        "python-dateutil": {"pandas"},
        "six": {"python-dateutil"},
    }
)
```

### Conversion to RequirementSet

**Location**: `src/pip/_internal/resolution/resolvelib/resolver.py:112`

**Process**:
1. Iterate through `result.mapping.values()` (sorted by extras)
2. For each candidate:
   - Get `InstallRequirement` via `candidate.get_install_requirement()`
   - Check if already installed
   - Set `should_reinstall` flag
   - Add to `RequirementSet`

**RequirementSet**:
```python
class RequirementSet:
    requirements: dict[str, InstallRequirement]  # Final install requirements
    # ... other fields for installation tracking
```

---

## Data Structure Summary Table

| Stage | Input Type | Output Type | Key Fields |
|-------|-----------|-------------|------------|
| **Command Line** | `str` | `InstallRequirement` | `req`, `link`, `constraint`, `user_supplied` |
| **Collection** | `InstallRequirement` | `Requirement` | `project_name`, `name`, `specifier` |
| **Candidate Discovery** | `Requirement` | `Candidate` | `name`, `version`, `source_link` |
| **Dependency Extraction** | `Candidate` | `Requirement[]` | From `dist.iter_dependencies()` |
| **Resolution State** | `Requirement[]` | `State` | `mapping`, `criteria`, `backtrack_causes` |
| **Backtracking** | `State` | `State` | Popped states, incompatibilities |
| **Final Result** | `State` | `Result` | `mapping`, `graph`, `criteria` |
| **Installation** | `Result` | `RequirementSet` | `requirements: dict[str, InstallRequirement]` |

---

## Key Type Definitions

### NormalizedName
```python
from pip._vendor.packaging.utils import NormalizedName
# Canonicalized package name (lowercase, normalized)
# Example: "pandas" → "pandas", "Pandas" → "pandas"
```

### Version
```python
from pip._vendor.packaging.version import Version
# PEP 440 version object
# Example: Version("2.3.3")
```

### SpecifierSet
```python
from pip._vendor.packaging.specifiers import SpecifierSet
# Version specifier set
# Example: SpecifierSet(">=1.22.4,<3.0")
```

### Link
```python
from pip._internal.models.link import Link
# Distribution file URL
# Example: Link("https://files.pythonhosted.org/.../pandas-2.3.3.whl")
```

### BaseDistribution
```python
from pip._internal.metadata import BaseDistribution
# Parsed package metadata
# Fields: canonical_name, version, requires_python, iter_dependencies(), etc.
```

---

## Memory and Performance Considerations

### Caching

**Location**: `src/pip/_internal/resolution/resolvelib/factory.py:111-117`

**Cached Objects**:
```python
self._link_candidate_cache: Cache[LinkCandidate] = {}
self._editable_candidate_cache: Cache[EditableCandidate] = {}
self._installed_candidate_cache: dict[str, AlreadyInstalledCandidate] = {}
self._extras_candidate_cache: dict[tuple[int, frozenset[NormalizedName]], ExtrasCandidate] = {}
```

**Purpose**: Avoid recreating candidate objects for the same link/package.

### State Management

- **State Stack**: Grows during resolution, shrinks during backtracking
- **Criteria**: Deep copied on `_push_new_state()`, may contain large candidate lists
- **Incompatibilities**: Accumulated over time, can grow large in complex conflicts

### Lazy Evaluation

- **Candidates**: `find_matches()` returns iterators, not lists
- **Dependencies**: `iter_dependencies()` is called only when candidate is pinned
- **Metadata**: Distribution metadata is fetched only when needed

---

## Example: Complete Data Flow

### Input: `pip install pandas`

1. **Command Line** → `InstallRequirement`:
   ```python
   InstallRequirement(
       req=Requirement("pandas"),
       user_supplied=True,
       constraint=False
   )
   ```

2. **Collection** → `SpecifierRequirement`:
   ```python
   SpecifierRequirement(
       _ireq=InstallRequirement(...),
       _extras=frozenset()
   )
   # name: "pandas"
   ```

3. **Candidate Discovery** → `LinkCandidate`:
   ```python
   LinkCandidate(
       link=Link("https://.../pandas-2.3.3.whl"),
       dist=BaseDistribution(name="pandas", version="2.3.3", ...)
   )
   ```

4. **Dependency Extraction** → `Requirement[]`:
   ```python
   [
       SpecifierRequirement("numpy>=1.22.4"),
       SpecifierRequirement("pytz>=2020.1"),
       RequiresPythonRequirement(">=3.9"),
       ...
   ]
   ```

5. **Resolution State**:
   ```python
   State(
       mapping={
           "pandas": LinkCandidate("pandas-2.3.3"),
           "<Python from Requires-Python>": RequiresPythonCandidate("3.10.0"),
           "numpy": LinkCandidate("numpy-2.2.6"),
           ...
       },
       criteria={
           "pandas": Criterion(candidates=[...], information=[...], incompatibilities=[]),
           ...
       },
       backtrack_causes=[]
   )
   ```

6. **Final Result**:
   ```python
   Result(
       mapping={...},  # All pinned candidates
       graph=DirectedGraph(...),  # Dependency tree
       criteria={...}  # Final criteria
   )
   ```

7. **RequirementSet**:
   ```python
   RequirementSet(
       requirements={
           "pandas": InstallRequirement(...),
           "numpy": InstallRequirement(...),
           ...
       }
   )
   ```

---

This document provides a complete specification of all data structures used in pip's dependency resolution system. Each structure is designed to support efficient backtracking, constraint resolution, and dependency graph construction.
