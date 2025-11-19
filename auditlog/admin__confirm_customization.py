"""
Confirm HR customizations for django-auditlog admin.

This module contains custom search functionality for the LogEntry admin interface.
"""
import re
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

# Try to import Elasticsearch - if not available, ES search will be disabled
try:
    from elasticsearch import Elasticsearch
    ELASTICSEARCH_AVAILABLE = True
except ImportError:
    ELASTICSEARCH_AVAILABLE = False


STRUCTURED_SEARCH_PATTERN = re.compile(
    r"^(?P<model_name>[A-Za-z_][A-Za-z0-9_]*):(?P<id>[1-9][0-9]*)$"
)


def perform_structured_search(request, queryset, search_term, message_user_func):
    """
    Perform structured search on LogEntry queryset.

    Structured search format: ModelName:ID
    Example: Person:123 or User:456

    This allows searching for all log entries related to a specific object
    by its model name and ID.

    Args:
        request: The Django request object
        queryset: The base LogEntry queryset to filter
        search_term: The search string (e.g., "Person:123")
        message_user_func: Function to display messages to user (from ModelAdmin)

    Returns:
        Tuple of (queryset, use_distinct) if structured search matches.
        Returns None if not a structured search pattern.
        Returns (queryset.none(), False) if the pattern matches but model/ID is invalid.
    """
    match = STRUCTURED_SEARCH_PATTERN.match(search_term)
    if not match:
        # Not a structured search, return None to indicate no match
        return None

    try:
        model_name = match.group("model_name")
        object_id = int(match.group("id"))
    except (ValueError, TypeError):
        # If the format is incorrect, return an empty queryset and show a message
        if not getattr(request, "_message_shown", False):
            message_user_func(
                request,
                "Structured search format must be 'ModelName:id'.",
                level="warning",
            )
            request._message_shown = True
        return queryset.none(), False

    # Attempt to retrieve the specified model
    try:
        # First try to get the model from the api app
        try:
            model = apps.get_model(app_label="api", model_name=model_name)
        except LookupError:
            # If not found in api, try to get User model specifically
            if model_name.lower() in ['user', 'customuser']:
                model = get_user_model()
            else:
                raise
        if not model:
            raise LookupError
    except LookupError:
        if not getattr(request, "_message_shown", False):
            message_user_func(
                request,
                f"Model '{model_name}' does not exist.",
                level="warning",
            )
            request._message_shown = True
        return queryset.none(), False

    # Filter log entries by content_type and object_id
    content_type = ContentType.objects.get_for_model(model)
    filtered_queryset = queryset.filter(
        content_type=content_type, object_id=object_id
    )

    return filtered_queryset, False


def perform_elasticsearch_search(request, queryset, search_term, message_user_func):
    """
    Perform full-text search using Elasticsearch with pagination support.

    This function searches the auditlog_logentry index in Elasticsearch
    and returns a queryset filtered by the matching IDs, ordered by
    Elasticsearch relevance score.

    Pagination:
    - Fetches all matching IDs from Elasticsearch (up to 10k limit)
    - Returns a Django queryset that supports .count() and slicing
    - Django admin handles pagination by slicing the queryset
    - Only the rows for the current page are fetched from the database

    If Elasticsearch is not available or not configured, returns None.

    Args:
        request: The Django request object
        queryset: The base LogEntry queryset to filter
        search_term: The search string
        message_user_func: Function to display messages to user (from ModelAdmin)

    Returns:
        Tuple of (queryset, use_distinct) if Elasticsearch search succeeds.
        Returns None if Elasticsearch is not available/configured.
    """
    from django.db.models import Case, When, FloatField, Value

    # Check if Elasticsearch is available
    if not ELASTICSEARCH_AVAILABLE:
        return None

    # Check if Elasticsearch is configured in settings
    if not hasattr(settings, 'ELASTICSEARCH_DSL'):
        return None

    try:
        # Initialize Elasticsearch client
        es_config = settings.ELASTICSEARCH_DSL.get("default", {})
        if not es_config:
            return None

        es = Elasticsearch(
            hosts=[es_config.get("hosts")],
            timeout=es_config.get("timeout", 30),
            max_retries=es_config.get("max_retries", 3),
            retry_on_timeout=es_config.get("retry_on_timeout", True),
        )

        # Check if index exists
        index_name = "auditlog_logentry"
        if not es.indices.exists(index=index_name):
            # Index doesn't exist, return None to show error
            return None

        # Perform Elasticsearch search
        # Search across all searchable fields with multi_match
        es_query = {
            "query": {
                "multi_match": {
                    "query": search_term,
                    "fields": [
                        "object_repr^2",  # Boost object_repr matches
                        "changes_searchable",
                        "actor_email",
                        "actor_first_name",
                        "actor_last_name",
                        "actor_username",
                    ],
                    "type": "best_fields",
                    "operator": "and",  # All terms must match
                    "fuzziness": "AUTO",  # Allow fuzzy matching for typos
                }
            },
            # Fetch all matching IDs up to limit
            # Django admin will paginate by slicing the queryset later
            "size": 10000,
            "_source": False,  # We only need IDs and scores
        }

        # Execute search
        response = es.search(index=index_name, body=es_query)

        # Extract matching IDs with their scores
        hits = response.get("hits", {}).get("hits", [])
        total_hits = response.get("hits", {}).get("total", {})

        # Get total count for warning message
        if isinstance(total_hits, dict):
            total_count = total_hits.get("value", 0)
        else:
            total_count = total_hits

        # Warn if results are truncated
        if total_count > 10000:
            message_user_func(
                request,
                f"Search returned {total_count:,} results, showing first 10,000. "
                "Please refine your search for better results.",
                level="warning",
            )

        if not hits:
            # No matches found, return empty queryset
            return queryset.none(), False

        # Extract just the IDs in relevance order
        matching_ids = [int(hit["_id"]) for hit in hits]

        # Filter queryset by matching IDs
        # Note: We can't preserve ES relevance order in Django queryset without expensive Case/When
        # Default to ordering by timestamp descending (most recent first)
        filtered_queryset = queryset.filter(id__in=matching_ids).order_by('-timestamp')

        # Django admin will paginate this queryset via slicing (e.g., queryset[0:100])
        # Only the rows for the current page will be fetched from the database
        return filtered_queryset, False

    except Exception as e:
        # If anything goes wrong with Elasticsearch, return None
        # This will trigger the error message in get_search_results()
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Elasticsearch search failed: {e}")
        return None
