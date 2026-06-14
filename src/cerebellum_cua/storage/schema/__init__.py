"""Packaged SQL schema files for the storage backends.

Holds the canonical PostgreSQL v4.2 DDL as package data so it is shipped inside
the built wheel and loadable via ``importlib.resources`` from an installed
package (not just a source checkout). See :mod:`cerebellum_cua.storage.postgres`.
"""
