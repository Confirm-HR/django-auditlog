"""
Django management command to test auditlog search functionality.

Usage:
    python manage.py test_auditlog_search --type structured --search "Person:42"
    python manage.py test_auditlog_search --type trigram --search "toagna"
    python manage.py test_auditlog_search --type trigram --search "john@example.com" --explain
"""

from django.core.management.base import BaseCommand
from django.db import connection
from auditlog.models import LogEntry
from auditlog.confirm_customizations import perform_structured_search, perform_trigram_search


class MockRequest:
    """Mock request object for testing structured search."""
    def __init__(self):
        self.messages = []


class Command(BaseCommand):
    help = 'Test auditlog search functions (structured and trigram)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            type=str,
            choices=['structured', 'trigram'],
            required=True,
            help='Type of search to perform: structured (ModelName:ID) or trigram (full-text)'
        )
        parser.add_argument(
            '--search',
            type=str,
            required=True,
            help='Search term to test'
        )
        parser.add_argument(
            '--explain',
            action='store_true',
            help='Show EXPLAIN ANALYZE output (PostgreSQL query plan)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Number of results to show (default: 10)'
        )

    def mock_message_user(self, request, message, level='info'):
        """Mock message_user function for structured search."""
        request.messages.append({'message': message, 'level': level})
        self.stdout.write(f"[{level.upper()}] {message}")

    def handle(self, *args, **options):
        search_type = options['type']
        search_term = options['search']
        explain = options['explain']
        limit = options['limit']

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS(f"Testing {search_type.upper()} search"))
        self.stdout.write(self.style.SUCCESS(f"Search term: '{search_term}'"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))

        # Get base queryset
        queryset = LogEntry.objects.all()

        # Perform search based on type
        if search_type == 'structured':
            mock_request = MockRequest()
            result = perform_structured_search(
                mock_request,
                queryset,
                search_term,
                self.mock_message_user
            )

            if result is None:
                self.stdout.write(self.style.ERROR("Not a valid structured search pattern!"))
                self.stdout.write("Expected format: ModelName:ID (e.g., Person:42)")
                return

            queryset, use_distinct = result

        elif search_type == 'trigram':
            if len(search_term) < 3:
                self.stdout.write(self.style.ERROR("Search term must be at least 3 characters!"))
                return

            queryset, use_distinct = perform_trigram_search(queryset, search_term)

        # Show query SQL (template - parameters not shown)
        self.stdout.write(self.style.WARNING("\nGenerated SQL (template):"))
        self.stdout.write(str(queryset.query))
        self.stdout.write("")

        # Show EXPLAIN ANALYZE if requested
        if explain:
            self.stdout.write(self.style.WARNING("\nEXPLAIN ANALYZE:"))
            with connection.cursor() as cursor:
                # Get the actual SQL with parameters from the queryset
                sql, params = queryset.query.sql_with_params()
                cursor.execute(f"EXPLAIN ANALYZE {sql}", params)
                for row in cursor.fetchall():
                    self.stdout.write(row[0])
            self.stdout.write("")

        # Execute query and show results
        self.stdout.write(self.style.SUCCESS(f"\nResults (showing first {limit}):"))
        count = queryset.count()
        self.stdout.write(f"Total matches: {count}\n")

        if count == 0:
            self.stdout.write(self.style.WARNING("No results found."))
            return

        for i, entry in enumerate(queryset[:limit], 1):
            self.stdout.write(f"{i}. [{entry.timestamp}] {entry.action} - {entry.object_repr}")
            if hasattr(entry, 'object_repr_similarity'):
                self.stdout.write(f"   Similarity scores: object_repr={entry.object_repr_similarity:.4f}, changes={entry.changes_similarity:.4f}")

        if count > limit:
            self.stdout.write(f"\n... and {count - limit} more results")

        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("Test completed successfully!"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))
