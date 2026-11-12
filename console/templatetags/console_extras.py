from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dictionary lookup in templates: {{ labels|get_item:key }}."""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
