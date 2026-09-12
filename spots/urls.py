from django.urls import path

from . import views

app_name = 'spots'

urlpatterns = [
    path('', views.map_view, name='map'),
    path('api/spots/', views.spots_api, name='spots_api'),
    path('spots/new/', views.spot_create, name='spot_create'),
    path('spots/<int:pk>/', views.spot_detail, name='spot_detail'),
    path('spots/<int:pk>/rate/', views.spot_rate, name='spot_rate'),
]
