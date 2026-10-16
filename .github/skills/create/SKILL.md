---
name: create
description: Create a new action in the NovaFlow action service. Guides through defining input/output models, implementing logic, registering the action, and writing tests. Use this when adding new functionality to your action service.
---

# Create Action

This skill helps you create a new action in your NovaFlow action service following best practices.

## Steps

1. **Ask for action details**:
   - Action name (will be shown in NovaFlow UI)
   - Description of what it does
   - Input parameters needed
   - Output data returned
   - Whether it's an action (returns data) or condition (returns boolean)

2. **Create action file** in `src/actions/`:

   ```python
   """<Action description>."""

   import loguru
   from novaflow.core import Router
   from pydantic import BaseModel, Field

   logger = loguru.logger
   router = Router()

   # Input/Output Models
   class <ActionName>Input(BaseModel):
       """Input model for <ActionName> action."""
       param: str = Field(..., description="Parameter description")

   class <ActionName>Output(BaseModel):
       """Output model for <ActionName> action."""
       result: str = Field(..., description="Result description")

   # Core Logic (testable, pure function)
   def process_<action_name>(param: str) -> dict:
       """Core logic for <action_name>."""
       # Implementation here
       return {"result": processed_value}

   # Action Registration
   @router.action(
       action_name="<ActionName>",
       input_model=<ActionName>Input,
       output_model=<ActionName>Output
   )
   async def <action_name>_action(params: <ActionName>Input) -> <ActionName>Output:
       """<Action description>."""
       logger.info(f"<ActionName> called with: {params}")
       result = process_<action_name>(param=params.param)
       logger.info(f"<ActionName> result: {result}")
       return <ActionName>Output(**result)
   ```

3. **Export router** in `src/actions/__init__.py`:

   ```python
   from .echo import router as echo_router
   from .<action_file> import router as <action_name>_router

   __all__ = ["echo_router", "<action_name>_router"]
   ```

4. **Include router** in `run.py`:

   ```python
   from src.actions import echo_router, <action_name>_router

   service.include_router(echo_router)
   service.include_router(<action_name>_router)
   ```

5. **Create tests** in `tests/test_<action_name>.py`:

   ```python
   import pytest
   from src.actions.<action_file> import process_<action_name>

   def test_<action_name>_basic():
       """Test basic functionality."""
       result = process_<action_name>(param="test")
       assert result["result"] == "expected"
   ```

6. **Run tests** to verify:
   ```bash
   uv run pytest
   ```

## Design Patterns

- **Separate logic from registration**: Core logic is a pure, testable function
- **Use Pydantic models**: Validate inputs/outputs with clear field descriptions
- **Use Field descriptions**: They appear in the NovaFlow UI for users
- **Log operations**: Use logger.info for debugging in production
- **One router per file**: Makes it easy to include/exclude actions

## Action Types

### Action (returns data)

```python
@router.action(action_name="ProcessData", input_model=Input, output_model=Output)
async def process_data(params: Input) -> Output:
    result = process_logic(params.value)
    return Output(**result)
```

### Condition (returns boolean)

```python
@router.condition(condition_name="CheckState", input_model=Input)
async def check_state(params: Input) -> dict:
    is_valid = validate_logic(params.value)
    return {"result": is_valid}  # Must return dict with "result" key
```

## Supported Types

NovaFlow has a strict type system. Only registered types are allowed.

### Primitive Types

- `str` - String values
- `int` - Integer numbers
- `float` - Floating-point numbers
- `bool` - Boolean values
- `Any` - Accepts any JSON-serializable value (bypasses validation)

### NovaFlow Custom Types

Import from `novaflow.types.data`:

- `Pose` - Position and orientation in 3D space
- `Vector3d` - 3D vector
- `MotionGroup` - Robot motion group identifier (format: `0@controller_name`)
- `Tcp` - Tool Center Point identifier
- `Joints` - Joint configuration (6-element float list for 6-axis robots)
- `JointTrajectory` - Trajectory data with joint positions and timing
- `PointCloudReference` - Reference to point cloud data (PLY format)
- `ImageReference` - Reference to image data
- `DepthImageReference` - Reference to 16-bit depth image
- `JsonReference` - Reference to JSON data

### List Nesting

Lists can be nested to arbitrary depth:

```python
list[Pose]              # Single list
list[list[Pose]]        # Nested lists
list[list[list[int]]]   # Triple nested
```

### Optional Fields

- **Input models**: Can use `Optional[T]` or `T | None` for optional parameters
- **Output models**: All fields MUST be required (no Optional allowed)

```python
from typing import Optional

class MyInput(BaseModel):
    required: str = Field(..., description="Required field")
    optional: Optional[Pose] = Field(None, description="Optional field")

class MyOutput(BaseModel):
    result: str = Field(..., description="All output fields must be required")
```

### Enums

Custom enums are fully supported:

```python
from enum import Enum

class RobotMode(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"

class Input(BaseModel):
    mode: RobotMode = Field(..., description="Operation mode")
```

## Types NOT Allowed

These types will be rejected at registration:

- ❌ `dict[K, V]` - Dictionaries not supported
- ❌ `set[T]` - Sets not supported
- ❌ `tuple[T, ...]` - Tuples not supported
- ❌ `Union[A, B]` - Union types not allowed (except Optional)
- ❌ Custom `BaseModel` types - Only registered NovaFlow types allowed

## Type System Rules

1. **Validation happens at registration** - Invalid types fail when the control plane receives your action schema
2. **No dynamic types** - All types must be known at registration time
3. **Output fields are strict** - No optional fields, no defaults in outputs
4. **Reference types are lightweight** - Types like `ImageReference` contain only object store keys, not actual data

## Common Patterns

### Action with NovaFlow types

```python
from novaflow.types.data import Pose, Vector3d, MotionGroup, Tcp
from typing import Optional

class RobotMoveInput(BaseModel):
    """Input for robot movement action."""
    target_pose: Pose = Field(..., description="Target pose to move to")
    offset: Vector3d = Field(..., description="Offset to apply")
    motion_group: MotionGroup = Field(..., description="Motion group (e.g., 0@ur10e)")
    tcp: Tcp = Field(..., description="Tool center point")
    speed: Optional[float] = Field(None, description="Optional speed override")

class RobotMoveOutput(BaseModel):
    """Output for robot movement action."""
    final_pose: Pose = Field(..., description="Final pose after movement")
    success: bool = Field(..., description="Movement succeeded")
```

### Action with list types

```python
from novaflow.types.data import Pose, ImageReference

class TrajectoryInput(BaseModel):
    waypoints: list[Pose] = Field(..., description="List of waypoint poses")
    images: list[ImageReference] = Field(..., description="Images captured at waypoints")

class TrajectoryOutput(BaseModel):
    executed: list[Pose] = Field(..., description="Actually executed waypoints")
    execution_times: list[float] = Field(..., description="Time taken per waypoint")
```

### Action with nested lists

```python
class MultiPathInput(BaseModel):
    """Multiple paths, each with multiple waypoints."""
    paths: list[list[Pose]] = Field(..., description="List of paths")

class MultiPathOutput(BaseModel):
    success: bool = Field(..., description="All paths executed")
    path_lengths: list[int] = Field(..., description="Number of waypoints per path")
```

### Action with Any type (flexible data)

```python
from typing import Any

class FlexibleInput(BaseModel):
    """Use Any for schema-less data."""
    config: Any = Field(..., description="Flexible configuration (any JSON)")
    parameters: list[Any] = Field(..., description="Heterogeneous parameter list")

class FlexibleOutput(BaseModel):
    result: Any = Field(..., description="Dynamic result")
```

### Action with Nova API access

```python
from wandelbots_nova import Nova
from novaflow.types.data import MotionGroup

def process_with_nova(motion_group: MotionGroup) -> dict:
    """Core logic using Nova API."""
    nova = Nova()
    # Use Nova API for robot control
    result = nova.some_operation(motion_group)
    return {"result": result, "success": True}
```

### Action with enums

```python
from enum import Enum

class GripperState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"

class GripperInput(BaseModel):
    state: GripperState = Field(..., description="Desired gripper state")
    force: float = Field(default=50.0, description="Gripping force percentage")

class GripperOutput(BaseModel):
    state: GripperState = Field(..., description="Actual gripper state")
    success: bool = Field(..., description="Command succeeded")
```

### Error handling

```python
async def my_action(params: Input) -> Output:
    """Action with proper error handling."""
    try:
        result = process_logic(params.value)
        return Output(**result)
    except Exception as e:
        logger.error(f"Action failed: {e}")
        raise  # Re-raise to report failure to control plane
```

## Common Mistakes

### ❌ Using dict types

```python
# WRONG - dict not supported
class BadInput(BaseModel):
    config: dict[str, str]  # Will be rejected
```

### ❌ Optional output fields

```python
# WRONG - output fields cannot be optional
class BadOutput(BaseModel):
    result: Optional[str] = None  # Will be rejected
```

### ❌ Union types (except Optional)

```python
from typing import Union

# WRONG - unions not supported
class BadInput(BaseModel):
    value: Union[Pose, Vector3d]  # Will be rejected
```

### ❌ Custom BaseModel types

```python
# WRONG - custom types must be in NovaFlow registry
class CustomType(BaseModel):
    x: float
    y: float

class BadInput(BaseModel):
    point: CustomType  # Will be rejected
```

### ✅ Correct alternatives

```python
# Use list of primitives instead of dict
class GoodInput(BaseModel):
    keys: list[str] = Field(..., description="Configuration keys")
    values: list[str] = Field(..., description="Configuration values")

# All output fields must be required
class GoodOutput(BaseModel):
    result: str = Field(..., description="Result (always present)")

# Use separate fields instead of unions
class GoodInput(BaseModel):
    use_pose: bool = Field(..., description="Whether to use pose or vector")
    pose: Optional[Pose] = Field(None, description="Pose if use_pose=True")
    vector: Optional[Vector3d] = Field(None, description="Vector if use_pose=False")

# Use existing NovaFlow types
from novaflow.types.data import Vector3d  # Already registered

class GoodInput(BaseModel):
    point: Vector3d = Field(..., description="3D point")
```

## Verification

After creating the action:

1. Run `uv run pytest` - all tests pass
2. Run `uv run run.py` - service starts without errors
3. Check logs for action registration confirmation
4. Deploy and test in NovaFlow UI (use `/install-action`)

## Troubleshooting

### Type validation errors

- **Symptom**: "Type not in registry" or "Unsupported type" errors
- **Solution**: Check that all types are in the supported types list above
- **Debug**: Look at the NovaFlow control plane logs when registering the action

### Optional field errors

- **Symptom**: "Output fields cannot be optional" error
- **Solution**: Remove `Optional` from all output model fields

### Import errors

- **Symptom**: Cannot import NovaFlow types
- **Solution**: Import from `novaflow.types.data`, e.g., `from novaflow.types.data import Pose`
