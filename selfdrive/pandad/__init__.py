try:
  from openpilot.selfdrive.pandad.pandad_api_impl import can_list_to_can_capnp, can_capnp_to_list
  assert can_list_to_can_capnp
  assert can_capnp_to_list
except (ImportError, OSError):
  # C extension(.so) 로드 실패 시 Python 구현으로 fallback
  import importlib.util as _ilu
  import os as _os
  _py = _os.path.join(_os.path.dirname(__file__), "pandad_api_impl.py")
  _spec = _ilu.spec_from_file_location("pandad_api_impl_py", _py)
  _mod = _ilu.module_from_spec(_spec)
  _spec.loader.exec_module(_mod)
  can_list_to_can_capnp = _mod.can_list_to_can_capnp
  can_capnp_to_list = _mod.can_capnp_to_list
