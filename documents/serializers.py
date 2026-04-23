from rest_framework import serializers

from .models import Document, Tenant


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ("id", "name", "slug", "rate_limit", "created_at")
        read_only_fields = fields


class DocumentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=1024)
    content = serializers.CharField()
    metadata = serializers.DictField(child=serializers.JSONField(), default=dict, required=False)

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("metadata must be a JSON object.")
        return value


class DocumentDetailSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant_id", read_only=True)

    class Meta:
        model = Document
        fields = ("id", "tenant_id", "title", "content", "metadata", "index_status", "is_deleted", "created_at", "updated_at")
        read_only_fields = fields


class DocumentListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ("id", "title", "metadata", "index_status", "created_at")
        read_only_fields = fields


class SearchHitSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    snippet = serializers.CharField(allow_blank=True, default="")
    score = serializers.FloatField()
    metadata = serializers.DictField(default=dict)
    highlights = serializers.DictField(default=dict)


class SearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField()
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    size = serializers.IntegerField()
    query_time_ms = serializers.IntegerField()
    cached = serializers.BooleanField()
    hits = SearchHitSerializer(many=True)
