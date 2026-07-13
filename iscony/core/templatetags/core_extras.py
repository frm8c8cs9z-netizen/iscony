from django import template


register = template.Library()


@register.filter
def dict_get(value, key):
    if not isinstance(value, dict):
        return ""

    return value.get(key, "")
