from django.urls import path

from .views import DocumentCreateView, DocumentDetailView, SearchView

urlpatterns = [
    # Documents
    path("documents", DocumentCreateView.as_view(), name="document-create"),
    path("documents/<str:doc_id>", DocumentDetailView.as_view(), name="document-detail"),

    # Search
    path("search", SearchView.as_view(), name="document-search"),
]
