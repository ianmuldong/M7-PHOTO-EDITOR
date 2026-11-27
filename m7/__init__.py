"""M7 Studio Editor: natural-language photo editing on top of the RapidRAW engine.

All pixel processing is done by RapidRAW's own non-destructive pipeline
(its headless ``export`` command). This package only translates intentions
into RapidRAW adjustment JSON, keeps edit history, and builds previews.
Nothing here uploads images anywhere.
"""

__version__ = "0.1.0"
