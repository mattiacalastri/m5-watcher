"""
plugin_loader.py — Zero-magic plugin discovery for m5-watcher.

Usage:
    from plugin_loader import discover_plugins
    tabs = discover_plugins()          # scans plugins/ by default
    tabs = discover_plugins("my_dir")  # custom dir

Each plugin registers itself via @register_tab decorator.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from textual.widget import Widget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry (module-level singleton, intentionally simple)
# ---------------------------------------------------------------------------

_REGISTRY: list["PluginTab"] = []


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PluginTab:
    """Descriptor for a plugin-supplied tab."""

    id: str
    label: str
    widget_factory: Callable[[], Widget]
    key_binding: str | None = None

    def __repr__(self) -> str:
        return f"PluginTab(id={self.id!r}, label={self.label!r}, key={self.key_binding!r})"


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def register_tab(
    id: str,
    label: str,
    key: str | None = None,
) -> Callable[[Callable[[], Widget]], Callable[[], Widget]]:
    """
    Decorator that registers a widget factory as a plugin tab.

    @register_tab(id="tab-foo", label="Foo", key="f")
    def make_widget() -> Widget:
        return MyWidget()
    """
    def decorator(factory: Callable[[], Widget]) -> Callable[[], Widget]:
        _REGISTRY.append(PluginTab(id=id, label=label, widget_factory=factory, key_binding=key))
        logger.debug("Registered plugin tab: %s (%s)", id, label)
        return factory

    return decorator


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_plugins(plugin_dir: Path | str | None = None) -> list[PluginTab]:
    """
    Scan *plugin_dir* (default: ``plugins/`` relative to this file) for
    ``*.py`` files, import each one, and return a snapshot of the registry.

    Import errors are logged as warnings and the offending file is skipped.
    Returns an empty list if the directory does not exist.
    """
    if plugin_dir is None:
        plugin_dir = Path(__file__).parent / "plugins"

    plugin_dir = Path(plugin_dir)

    if not plugin_dir.exists():
        logger.warning("Plugin directory does not exist: %s", plugin_dir)
        return []

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # skip __init__.py and private files

        module_name = f"_m5_plugin_{py_file.stem}"

        # Skip if already imported (hot-reload not needed here)
        if module_name in sys.modules:
            continue

        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            logger.warning("Cannot create module spec for: %s", py_file)
            continue

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            logger.debug("Loaded plugin: %s", py_file.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load plugin %s: %s", py_file.name, exc)
            del sys.modules[module_name]

    return list(_REGISTRY)


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------

def clear_registry() -> None:
    """Reset the global registry. Intended for use in tests only."""
    _REGISTRY.clear()
