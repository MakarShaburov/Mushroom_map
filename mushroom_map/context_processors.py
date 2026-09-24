from django.conf import settings


def carto_api_key(request):
    return {'carto_api_key': settings.CARTO_API_KEY}
