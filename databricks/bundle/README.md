# Club Data Platform bundle

This Databricks Declarative Automation Bundle represents the
`clubdata_match_insights_pipeline` Lakeflow job with local notebook and dbt
sources.

The bundle has one development target and uses the Databricks default
user-specific workspace root. It intentionally contains no schedule or trigger.

## Validate

Set the SQL warehouse ID for the current shell without adding it to a file:

```sh
export BUNDLE_VAR_sql_warehouse_id="<warehouse-id>"
databricks bundle validate -t dev --profile clubdata-free
databricks bundle summary -t dev --profile clubdata-free
```

Run these commands from `databricks/bundle`. Do not commit warehouse IDs or
credentials. The `dev` target can be deployed as a separate development job.
The development job must never be bound to or replace the existing main job.