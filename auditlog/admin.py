import re
from functools import cached_property

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import TrigramSimilarity
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import BooleanField, Q, TextField, Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import Cast
from django.utils.translation import gettext_lazy as _

from auditlog.filters import CIDFilter, ResourceTypeFilter
from auditlog.mixins import LogEntryAdminMixin
from auditlog.models import LogEntry

STRUCTURED_SEARCH_PATTERN = re.compile(
    r"^(?P<model_name>[A-Za-z_][A-Za-z0-9_]*):(?P<id>[1-9][0-9]*)$"
)


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin, LogEntryAdminMixin):
    date_hierarchy = "timestamp"
    list_select_related = ["content_type", "actor"]
    list_display = [
        "created",
        "resource_url",
        "action",
        "msg_short",
        "user_url",
        "cid_url",
    ]
    # Search fields optimized with PostgreSQL trigram (pg_trgm) indices
    # Migration 0019_add_trigram_indices adds GIN indices on object_repr and changes
    # This enables fast ILIKE searches even on millions of rows
    search_fields = [
        "object_repr",  # Indexed with gin_trgm_ops - fast text search
        "changes",  # Indexed with gin_trgm_ops - fast JSON text search
        "actor__first_name",
        "actor__last_name",
        f"actor__{get_user_model().USERNAME_FIELD}",
        # Note: timestamp removed - use date_hierarchy or filters for date-based search
    ]
    list_filter = ["action", ResourceTypeFilter, CIDFilter]
    readonly_fields = ["created", "resource_url", "action", "user_url", "msg"]
    fieldsets = [
        (None, {"fields": ["created", "user_url", "resource_url", "cid"]}),
        (_("Changes"), {"fields": ["action", "msg"]}),
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @cached_property
    def _own_url_names(self):
        return [pattern.name for pattern in self.urls if pattern.name]

    def has_delete_permission(self, request, obj=None):
        if (
            request.resolver_match
            and request.resolver_match.url_name not in self._own_url_names
        ):
            # only allow cascade delete to satisfy delete_related flag
            return super().has_delete_permission(request, obj)
        return False

    def get_search_results(self, request, queryset, search_term):
        """
        Override Django admin search to:
        1. Handle structured search (ModelName:ID) - uses content_type/object_id indices
        2. Use trigram % operator for free-text filtering - uses GIN indices
        3. Use TrigramSimilarity for ranking results by relevance
        """
        # Check for structured search pattern first
        if search_term:
            match = STRUCTURED_SEARCH_PATTERN.match(search_term)
            if match:
                # Structured search logic (same as before)
                try:
                    model_name = match.group("model_name")
                    object_id = int(match.group("id"))

                    try:
                        try:
                            model = apps.get_model(
                                app_label="api", model_name=model_name
                            )
                        except LookupError:
                            if model_name.lower() in ["user", "customuser"]:
                                model = get_user_model()
                            else:
                                raise
                    except LookupError:
                        self.message_user(
                            request,
                            f"Model '{model_name}' does not exist.",
                            level="warning",
                        )
                        return queryset.none(), False

                    # Filter using indexed fields
                    content_type = ContentType.objects.get_for_model(model)
                    queryset = queryset.filter(
                        content_type=content_type, object_id=object_id
                    )
                    return queryset, False  # False = don't use distinct()

                except (ValueError, TypeError):
                    self.message_user(
                        request,
                        "Structured search format must be 'ModelName:id'.",
                        level="warning",
                    )
                    return queryset.none(), False

        # Use trigram similarity for free-text search (uses GIN indices from migration 0019)
        # Requires minimum 3 characters for meaningful trigram matching
        if search_term and len(search_term) >= 3:
            # CRITICAL: Use the % operator via RawSQL for filtering to ensure index usage
            # Then use TrigramSimilarity for ranking/ordering
            # The % operator WILL use the GIN index, while similarity() function alone may not

            # Filter using % operator (uses GIN indices) for ALL fields
            # Using RawSQL for proper parameter binding with .annotate()
            # IMPORTANT: Combine all filters BEFORE annotating to avoid SQL errors from mismatched columns

            # Build query using mix of RawSQL and Django ORM trigram lookups
            # Migration 0020 adds trigram indices on User model fields (first_name, last_name, username)
            user_model = get_user_model()
            username_field = user_model.USERNAME_FIELD

            # For EXACT substring matching (emails, names, departments), use icontains
            # The GIN trigram indices from migrations 0019 & 0020 accelerate ILIKE queries
            # This finds exact occurrences, not fuzzy matches
            queryset = queryset.filter(
                Q(object_repr__icontains=search_term) |
                Q(changes__icontains=search_term) |
                Q(actor__first_name__icontains=search_term) |
                Q(actor__last_name__icontains=search_term) |
                Q(**{f"actor__{username_field}__icontains": search_term})
            ).annotate(
                object_repr_similarity=TrigramSimilarity("object_repr", search_term),
                changes_similarity=TrigramSimilarity(Cast("changes", TextField()), search_term),
            ).order_by("-object_repr_similarity", "-changes_similarity")

            return queryset.distinct(), False  # False = don't use additional distinct()

        # For very short searches (< 3 chars), return no results with a helpful message
        # Trigram matching is ineffective on very short strings
        if search_term:
            self.message_user(
                request,
                "Please enter at least 3 characters for text search, or use structured search (ModelName:ID).",
                level="warning",
            )
            return queryset.none(), False

        # No search term - return all results
        return super().get_search_results(request, queryset, search_term)

    def get_queryset(self, request):
        """Get base queryset. Search logic is handled in get_search_results()."""
        self.request = request
        return super().get_queryset(request=request)
