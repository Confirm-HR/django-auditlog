from functools import cached_property

from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

from auditlog.confirm_customizations import perform_structured_search, perform_trigram_search
from auditlog.filters import CIDFilter, ResourceTypeFilter
from auditlog.mixins import LogEntryAdminMixin
from auditlog.models import LogEntry


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
        2. Use plain ILIKE for free-text filtering - uses GIN trigram indices
        3. Use TrigramSimilarity for ranking results by relevance
        """
        # Check for structured search pattern first (ModelName:ID)
        if search_term:
            result = perform_structured_search(
                request, queryset, search_term, self.message_user
            )
            if result is not None:
                return result

        # Use trigram similarity for free-text search (uses GIN indices from migrations 0019 & 0020)
        # Requires minimum 3 characters for meaningful trigram matching
        if search_term and len(search_term) >= 3:
            return perform_trigram_search(queryset, search_term)

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
