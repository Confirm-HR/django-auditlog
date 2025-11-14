# Migration to add PostgreSQL trigram (pg_trgm) indices for fast text search
# This dramatically improves search performance on large tables (3M+ rows)
#
# IMPORTANT: Uses CREATE INDEX CONCURRENTLY to avoid table locks during index creation
# This is safe to run in production without downtime

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    # CRITICAL: atomic = False is required for CREATE INDEX CONCURRENTLY
    # CONCURRENTLY cannot run inside a transaction block
    atomic = False

    dependencies = [
        ("auditlog", "0018_merge_upstream_and_confirm"),
    ]

    operations = [
        # Enable the pg_trgm extension (required for trigram indices)
        TrigramExtension(),

        # Add GIN index on object_repr for fast text search
        # CONCURRENTLY avoids table locks - safe for production deployment
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS auditlog_logentry_object_repr_trgm_idx
                ON auditlog_logentry USING gin (object_repr gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS auditlog_logentry_object_repr_trgm_idx;",
        ),

        # Add GIN index on changes JSONField (cast to text) for fast text search
        # CONCURRENTLY avoids table locks - safe for production deployment
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS auditlog_logentry_changes_trgm_idx
                ON auditlog_logentry USING gin ((changes::text) gin_trgm_ops);
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS auditlog_logentry_changes_trgm_idx;",
        ),
    ]
