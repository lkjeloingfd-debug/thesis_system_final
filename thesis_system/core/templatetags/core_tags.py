from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Look up a saved evaluation score using a criterion's database ID."""
    return dictionary.get(key, "")
