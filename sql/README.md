# SQL schema

`cerebellum_cua_v42_schema.sql` is the canonical PostgreSQL v4.2 DDL, kept here
as a convenient top-level reference.

The copy that ships inside the installed package (and that the Postgres backend
loads at runtime via `importlib.resources`) lives at
`src/cerebellum_cua/storage/schema/cerebellum_cua_v42_schema.sql`. Keep the two
in sync when editing the schema; the packaged copy is the source of truth for
installed users.
