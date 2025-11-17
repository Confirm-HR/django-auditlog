"""
Confirm HR customizations for django-auditlog.

This module contains custom lookups, search logic, and other extensions
specific to Confirm HR's requirements.
"""

import re
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Lookup, Q, TextField
from django.db.models.fields import CharField, TextField as TextFieldModel
from django.db.models.lookups import PatternLookup
from django.db.models.functions import Cast


# Custom lookup that generates plain ILIKE without UPPER() transformation
# ILIKE is already case-insensitive, UPPER() is redundant and prevents trigram index usage
@CharField.register_lookup
@TextFieldModel.register_lookup
class PlainIContains(PatternLookup):
    """
    Generates: field ILIKE '%pattern%'
    Instead of: UPPER(field::text) LIKE UPPER('%pattern%')

    ILIKE is already case-insensitive. UPPER() prevents GIN trigram index usage.
    Inherits from PatternLookup to properly handle wildcard pattern preparation.
    """
    lookup_name = 'plain_icontains'
    param_pattern = '%%%s%%'  # Wrap with wildcards for ILIKE

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        params = lhs_params + rhs_params
        # Generate plain ILIKE without UPPER() transformation
        return f'{lhs} ILIKE {rhs}', params


# Structured search pattern: ModelName:ID
STRUCTURED_SEARCH_PATTERN = re.compile(
    r"^(?P<model_name>[A-Za-z_][A-Za-z0-9_]*):(?P<id>[1-9][0-9]*)$"
)


def perform_structured_search(request, queryset, search_term, message_user_func):
    """
    Handle structured search with pattern: ModelName:ID

    Args:
        request: Django request object
        queryset: Base queryset to filter
        search_term: Search string from user
        message_user_func: Function to display messages to user

    Returns:
        tuple: (filtered_queryset, use_distinct) or None if not a structured search
    """
    match = STRUCTURED_SEARCH_PATTERN.match(search_term)
    if not match:
        return None

    try:
        model_name = match.group("model_name")
        object_id = int(match.group("id"))

        try:
            # First try to get the model from the api app
            try:
                model = apps.get_model(app_label="api", model_name=model_name)
            except LookupError:
                # Special handling for User models
                if model_name.lower() in ["user", "customuser"]:
                    model = get_user_model()
                else:
                    raise
        except LookupError:
            message_user_func(
                request,
                f"Model '{model_name}' does not exist.",
                level="warning",
            )
            return queryset.none(), False

        # Filter using indexed fields (content_type, object_id)
        content_type = ContentType.objects.get_for_model(model)
        queryset = queryset.filter(
            content_type=content_type, object_id=object_id
        )
        return queryset, False  # False = don't use distinct()

    except (ValueError, TypeError):
        message_user_func(
            request,
            "Structured search format must be 'ModelName:id'.",
            level="warning",
        )
        return queryset.none(), False


def perform_trigram_search(queryset, search_term):
    """
    Perform full-text search using GIN trigram indices.

    Uses plain_icontains lookup which generates plain ILIKE without UPPER()
    transformation. This allows GIN trigram indices to be used.

    Args:
        queryset: Base queryset to filter
        search_term: Search string from user

    Returns:
        tuple: (filtered_queryset, use_distinct)
    """
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD

    # For EXACT substring matching (emails, names, departments), use plain_icontains
    # This generates plain ILIKE without UPPER() transformation
    # ILIKE is already case-insensitive; UPPER() prevents trigram index usage
    # GIN trigram indices from migrations 0019 & 0020 accelerate plain ILIKE queries

    # Use UNION to get IDs efficiently using indices, then filter by those IDs
    # This allows the final queryset to support select_related() which Django admin requires

    from django.db.models import BooleanField
    from django.db.models.expressions import RawSQL

    # Query 1: Search in LogEntry fields (object_repr, changes) - uses trigram indices
    logentry_ids = queryset.filter(
        Q(object_repr__plain_icontains=search_term) |
        Q(RawSQL("(changes)::text ILIKE %s", (f'%{search_term}%',), output_field=BooleanField()))
    ).values_list('id', flat=True)

    # Query 2: Search in actor fields (requires join) - uses user trigram indices
    actor_ids = queryset.filter(
        Q(actor__first_name__plain_icontains=search_term) |
        Q(actor__last_name__plain_icontains=search_term) |
        Q(**{f"actor__{username_field}__plain_icontains": search_term})
    ).values_list('id', flat=True)

    # Combine IDs with UNION to get unique set of matching IDs
    combined_ids = logentry_ids.union(actor_ids)

    # Filter original queryset by the matched IDs and add similarity annotations
    # This returns a normal queryset that supports select_related()
    result_q = queryset.filter(id__in=combined_ids).annotate(
        object_repr_similarity=TrigramSimilarity("object_repr", search_term),
        changes_similarity=TrigramSimilarity(Cast("changes", TextField()), search_term),
    ).order_by("-object_repr_similarity", "-changes_similarity")

    return result_q, False  # False = don't use additional distinct()
