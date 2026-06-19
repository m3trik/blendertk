# !/usr/bin/python
# coding=utf-8
"""Switchboard slot wiring for the RizomUV Bridge panel (``rizom_bridge.ui``).

Mirror of mayatk's ``uv_utils.rizom_bridge.rizom_bridge_slots``. Blender's bridge is thin
(one-way send, no preset/param/round-trip machinery), so this inherits the engine directly
rather than uitk's ``BridgeSlotsBase``. Discovered by ``BlenderUiHandler``
(``marking_menu.show("rizom_bridge")``)."""
import blendertk as btk
from blendertk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge


class RizomBridgeSlots(RizomUVBridge):
    """Slots wired to ``rizom_bridge.ui``."""

    def __init__(self, switchboard, log_level="WARNING"):
        RizomUVBridge.__init__(self)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.rizom_bridge
        self.set_log_level(log_level)
        try:
            self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            self.logger.setup_logging_redirect(self.ui.txt000)
        except Exception:
            pass
        self.ui.txt000.setText("Select mesh object(s), set load options, then Send to RizomUV.")

    def header_init(self, widget):
        """Configure header help text."""
        from uitk.widgets.mixins.tooltip_mixin import fmt

        widget.set_help_text(
            fmt(
                title="RizomUV Bridge",
                body="Export the selected meshes to FBX and open them in a fresh RizomUV session "
                "(one-way send — RizomUV stays open for interactive UV work).",
                steps=[
                    "Select mesh object(s).",
                    "Toggle the load options below.",
                    "Click <b>Send to RizomUV</b> — RizomUV launches with the meshes loaded.",
                ],
                sections=[
                    ("Notes", [
                        "RizomUV (Rizom Lab) must be installed — auto-discovered under "
                        "<code>Program Files\\Rizom Lab</code>.",
                        "Windows only. No automatic UV round-trip back into Blender — save in "
                        "RizomUV and re-import when done.",
                    ]),
                ],
            )
        )

    def b000(self):
        """Send to RizomUV."""
        selection = btk.selected_objects()
        if not selection:
            self.sb.message_box("<hl>Nothing selected</hl><br>Select mesh object(s) to send.")
            return
        if not self.rizom_path:
            self.sb.message_box(
                "<hl>RizomUV not found</hl><br>Install RizomUV (Rizom Lab) to use this bridge."
            )
            return
        self.ui.txt000.clear()
        try:
            self.send(
                selection,
                load_uvs=self.ui.chk000.isChecked(),
                import_groups=self.ui.chk001.isChecked(),
                load_uvw_props=self.ui.chk002.isChecked(),
                load_textures=self.ui.chk003.isChecked(),
            )
        except Exception as error:
            import traceback

            self.logger.error(f"Send to RizomUV failed: {error}")
            self.ui.txt000.append(f"<font color='red'>{traceback.format_exc()}</font>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from blendertk.ui_utils.blender_ui_handler import BlenderUiHandler

    ui = BlenderUiHandler.instance().get("rizom_bridge", reload=True)
    ui.show(pos="screen", app_exec=True)
