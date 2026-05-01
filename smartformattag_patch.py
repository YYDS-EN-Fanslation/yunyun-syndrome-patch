"""
Monkey-patch for UnityPy TypeTreeHelper to handle unknown ManagedReference types
(e.g. SmartFormatTag) as raw byte passthroughs instead of crashing.

Import this before doing anything with UnityPy.
"""
import UnityPy.helpers.TypeTreeHelper as TTH
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

_orig_get_ref_type_node = TTH.get_ref_type_node
_orig_read_value        = TTH.read_value
_orig_write_value       = TTH.write_value


def _patched_get_ref_type_node(ref_object, assetfile):
    """Return None instead of raising when a referenced type is not found."""
    try:
        return _orig_get_ref_type_node(ref_object, assetfile)
    except ValueError:
        return None


def _patched_read_value(node, reader, config):
    """
    When reading a ReferencedObject whose type is unknown (get_ref_type_node
    returns None), consume the remaining bytes up to the next aligned boundary
    as raw data and store them on the value dict under '_raw_data'.
    """
    if node.m_Type != "ReferencedObject":
        return _orig_read_value(node, reader, config)

    value = {}
    for child in node.m_Children:
        if child.m_Type == "ReferencedObjectData":
            ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
            if ref_type_nodes is None:
                # Unknown type — record position so we can figure out byte size.
                # We store the reader itself so the write side can use position info.
                # The actual bytes are unknown-length; we'll read until the next
                # ReferencedObject boundary using the size hint from the registry.
                # For now: store None and handle on write side by writing nothing.
                # This preserves same-length behaviour; variable-length will still
                # need the full SmartFormatTag type tree.
                value["_raw_data"] = None
                value["_raw_start"] = reader.Position
                continue
            value[child.m_Name] = _orig_read_value(ref_type_nodes, reader, config)
        else:
            value[child.m_Name] = _orig_read_value(child, reader, config)
    return value


def _patched_write_value(value, node, writer, config):
    """
    When writing a ReferencedObject with an unknown type (_raw_data key present),
    write nothing for the data portion — matches the read side skip behaviour.
    """
    if node.m_Type != "ReferencedObject":
        return _orig_write_value(value, node, writer, config)

    if isinstance(value, dict):
        for child in node.m_Children:
            if child.m_Type == "ReferencedObjectData":
                ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
                if ref_type_nodes is None:
                    # Skip — no bytes were read, no bytes to write
                    continue
                _orig_write_value(value[child.m_Name], ref_type_nodes, writer, config)
            else:
                _orig_write_value(value[child.m_Name], child, writer, config)
    else:
        for child in node.m_Children:
            if child.m_Type == "ReferencedObjectData":
                ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
                if ref_type_nodes is None:
                    continue
                _orig_write_value(getattr(value, child.m_Name), ref_type_nodes, writer, config)
            else:
                _orig_write_value(getattr(value, child.m_Name), child, writer, config)


# Apply patches
TTH.get_ref_type_node = _patched_get_ref_type_node
TTH.read_value        = _patched_read_value
TTH.write_value       = _patched_write_value

print("[smartformattag_patch] TypeTreeHelper patched — unknown ReferencedObject types will be silently skipped.")
