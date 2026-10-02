from django.urls import path

from . import views

app_name = 'spots'

urlpatterns = [
    path('', views.map_view, name='map'),
    path('api/spots/', views.spots_api, name='spots_api'),
    path('spots/new/', views.spot_create, name='spot_create'),
    path('spots/<int:pk>/', views.spot_detail, name='spot_detail'),
    path('spots/<int:pk>/panel/', views.spot_panel, name='spot_panel'),
    path('spots/<int:pk>/card/', views.spot_search_card, name='spot_search_card'),
    path('spots/<int:pk>/review/', views.spot_review_create, name='spot_review_create'),
    path('spots/<int:pk>/comments/', views.spot_comment_create, name='spot_comment_create'),
    path('comments/<int:comment_id>/vote/', views.comment_vote, name='comment_vote'),
    path('spots/<int:pk>/rate/', views.spot_rate, name='spot_rate'),
    path('spots/<int:pk>/edit/', views.spot_edit, name='spot_edit'),
    path('spots/<int:pk>/delete/', views.spot_delete, name='spot_delete'),
]
