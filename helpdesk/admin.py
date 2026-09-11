from django.contrib import admin

from helpdesk.models import (
    Comment,
    HelpdeskRestrictionGroup,
    PushSubscription,
    Ticket,
    TicketAttachment,
    TicketCategory,
    TicketContestation,
    TicketSpecificCategory,
)


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    readonly_fields = ('created_at',)
    autocomplete_fields = ('author',)


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    readonly_fields = ('created_at',)


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('created_at',)


@admin.register(TicketSpecificCategory)
class TicketSpecificCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('created_at',)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'category', 'specific_category', 'status', 'priority',
        'requester_name', 'assigned_to', 'is_archived', 'created_at',
    )
    list_filter = ('status', 'priority', 'category', 'is_archived', 'is_active', 'is_rejected')
    search_fields = ('title', 'requester_name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('category', 'specific_category', 'equipe', 'requester_user', 'created_by', 'assigned_to', 'resolved_by')
    filter_horizontal = ('tags',)
    inlines = (CommentInline, TicketAttachmentInline)
    date_hierarchy = 'created_at'


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'author', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('text', 'ticket__title', 'author__username')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('ticket', 'author')


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'file_name', 'created_at')
    search_fields = ('file_name', 'ticket__title')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('ticket',)


@admin.register(TicketContestation)
class TicketContestationAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'contested_by', 'finalized_by', 'was_rejected', 'created_at')
    list_filter = ('was_rejected',)
    search_fields = ('reason', 'ticket__title')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('ticket', 'contested_by', 'finalized_by')


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('user__username', 'endpoint')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('user',)


@admin.register(HelpdeskRestrictionGroup)
class HelpdeskRestrictionGroupAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo', 'created_at', 'updated_at')
    list_filter = ('ativo',)
    search_fields = ('nome',)
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('usuarios_restritos', 'visualizadores')
