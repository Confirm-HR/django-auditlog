from functools import cached_property

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

from auditlog.admin__confirm_customization import (
    perform_structured_search,
    perform_elasticsearch_search,
)
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
    search_fields = [
        "timestamp",
        "object_repr",
        "changes",
        "actor__first_name",
        "actor__last_name",
        f"actor__{get_user_model().USERNAME_FIELD}",
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

    def get_queryset(self, request):
        self.request = request
        return super().get_queryset(request=request)

    def get_search_results(self, request, queryset, search_term):
        """
        Override Django admin search to support custom search modes.

        Search priority:
        1. Structured search (ModelName:ID format, e.g., "Person:123")
        2. Elasticsearch full-text search (if available and configured)
        3. ERROR - no fallback to Django default search (would cause SeqScan/OOM)

        Returns:
            tuple: (queryset, use_distinct)
        """
        if not search_term:
            # No search term, return full queryset
            return queryset, False

        # Try structured search first (ModelName:ID)
        result = perform_structured_search(
            request, queryset, search_term, self.message_user
        )
        if result is not None:
            # Structured search matched (either found results or returned empty)
            return result

        # Try Elasticsearch full-text search
        result = perform_elasticsearch_search(
            request, queryset, search_term, self.message_user
        )
        if result is not None:
            # Elasticsearch is available and returned results (could be empty but valid)
            return result

        # Neither structured search nor Elasticsearch available
        # Show error instead of falling back to Django search (would cause SeqScan/OOM)
        self.message_user(
            request,
            "Search is not available. Please use structured search (ModelName:ID) "
            "or contact support if Elasticsearch is not configured.",
            level="error",
        )
        return queryset.none(), False
