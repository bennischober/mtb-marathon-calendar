# Shared data contract

`event.schema.json` is the versioned contract between the Python data pipeline and the Astro site.

The pipeline validates source observations against this shape before exporting site-facing JSON. The site consumes the exported artifact and does not import Python code or query the database.

When the model changes:

1. Update the JSON Schema.
2. Update the Pydantic models in `services/pipeline/src/mtb_pipeline/models.py`.
3. Update fixtures and generated data.
4. Run the pipeline tests before building the site.