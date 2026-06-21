# blendertk — API Changes

_Diff vs prior baseline. Generated 2026-06-21._

## Removed (15)

- `env_utils/maya_bridge/templates/import_and_frame.py::main` — was `()`
- `env_utils/maya_bridge/templates/new_scene.py::main` — was `()`
- `env_utils/unity_bridge/_unity_bridge.py::UnityBridge` — was `(class)`
- `env_utils/unity_bridge/_unity_bridge.py::UnityBridge.list_template_modes` — was `(self)`
- `env_utils/unity_bridge/_unity_bridge.py::UnityBridge.params_defaults` — was `(self)`
- `env_utils/unity_bridge/_unity_bridge.py::list_delivery_modes` — was `() -> List[Tuple[str, str]]`
- `env_utils/unity_bridge/parameters.py::defaults` — was `() -> 'dict[str, Any]'`
- `env_utils/unity_bridge/parameters.py::referenced_keys` — was `(script_text: str) -> 'set[str]'`
- `env_utils/unity_bridge/parameters.py::render_context` — was `(values: 'dict[str, Any]') -> 'dict[str, str]'`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots` — was `(class)`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots.b000` — was `(self)`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots.list_template_modes` — was `(self)`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots.make_bridge` — was `(self) -> UnityBridge`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots.params_module` — was `(self)`
- `env_utils/unity_bridge/unity_bridge_slots.py::UnityBridgeSlots.template_dir` — was `(self) -> Path`
