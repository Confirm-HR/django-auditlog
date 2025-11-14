# Generated manually to merge upstream and Confirm HR fork migrations

from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge migration to reconcile two parallel migration branches:
    - Confirm HR fork: 0016_alter_logentry_serialized_data -> 0017_logentry_auditlog_contenttype_objid_idx
    - Upstream: 0016_logentry_remote_port -> 0017_add_actor_email

    This migration has no operations - it only merges the dependency tree.
    """

    dependencies = [
        ('auditlog', '0017_logentry_auditlog_contenttype_objid_idx'),
        ('auditlog', '0017_add_actor_email'),
    ]

    operations = [
        # No operations needed - this is a merge-only migration
    ]
