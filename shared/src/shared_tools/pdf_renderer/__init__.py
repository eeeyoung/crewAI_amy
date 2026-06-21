"""PDF Renderer — HTML/CSS to PDF generation via Playwright + Jinja2.

Replaces the legacy Excel COM automation approach with cross-platform,
design-controllable PDF generation from Jinja2 HTML templates.

Provides:
  - PdfRendererService: QObject service (threaded + sync modes)
  - render_pdf(): convenience function for one-shot rendering
  - calculate_vo_costs(): VO cost math (10% margin, 10% GST)
  - calculate_po_totals(): PO cost math (10% GST)
"""

from .pdf_renderer_service import (
    PdfRendererService,
    render_pdf,
    calculate_vo_costs,
    calculate_po_totals,
)

__all__ = [
    "PdfRendererService",
    "render_pdf",
    "calculate_vo_costs",
    "calculate_po_totals",
]
