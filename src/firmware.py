from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parent.parent
ARDUINO_CLI = "arduino-cli"
NANO_EVERY_FQBN = "arduino:megaavr:nona4809"

SKETCH_PATHS = {
    "generic": REPO_ROOT / "ardiuno" / "generic",
    "roller": REPO_ROOT / "ardiuno" / "roller",
    "dispenser": REPO_ROOT / "ardiuno" / "dispenser",
}


def available_firmware_options(station_name):
    """Return station-appropriate firmware choices for the UI."""
    options = {}
    if station_name in SKETCH_PATHS:
        options[station_name] = f"{station_name}.ino"
    options["generic"] = "generic.ino"
    return options


def flash_firmware(port, sketch_key):
    """Compile and upload a sketch to the given serial port."""
    sketch_path = SKETCH_PATHS.get(sketch_key)
    if sketch_path is None:
        return {"ok": False, "output": f"Unknown sketch: {sketch_key}"}

    commands = [
        [ARDUINO_CLI, "compile", "--fqbn", NANO_EVERY_FQBN, str(sketch_path)],
        [ARDUINO_CLI, "upload", "-p", port, "--fqbn", NANO_EVERY_FQBN, str(sketch_path)],
    ]

    output_parts = []
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "output": "\n\n".join(output_parts + [str(exc)])}

        joined = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()
        output_parts.append(joined or f"{' '.join(cmd)} completed with no output.")

        if result.returncode != 0:
            return {"ok": False, "output": "\n\n".join(output_parts)}

    return {"ok": True, "output": "\n\n".join(output_parts)}
