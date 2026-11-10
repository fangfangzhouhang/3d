"""Deployment extension point; neither export nor target performance is validated."""
from __future__ import annotations


def export_onnx(model, destination, *, example_input, options=None):
    """TODO: implement only after the deployment model and export contract are agreed.

    Target context: CPU-only i3-5005U. Architecture, supported operations, input
    shape, precision, runtime and acceptable latency remain undecided.
    """
    raise NotImplementedError(
        "ONNX export is pending a model/runtime/input-shape decision for the CPU-only "
        "i3-5005U target; no export or deployment validation has been performed."
    )
