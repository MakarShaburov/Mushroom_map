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
    path('spots/<int:pk>/report/form/', views.spot_report_form, name='spot_report_form'),
    path('spots/<int:pk>/reports/new/', views.spot_report_create, name='spot_report_create'),
    path('spots/<int:pk>/reports/', views.spot_reports_page, name='spot_reports_page'),
    path('spots/reports/<int:report_id>/delete/', views.spot_report_delete, name='spot_report_delete'),
    path('types/<int:pk>/card/', views.mushroom_type_card, name='mushroom_type_card'),
    path('photos/like/', views.photo_like_toggle, name='photo_like_toggle'),
    path('spots/<int:pk>/comments/', views.spot_comment_create, name='spot_comment_create'),
    path('comments/<int:comment_id>/vote/', views.comment_vote, name='comment_vote'),
    path('spots/<int:pk>/rate/', views.spot_rate, name='spot_rate'),
    path('spots/<int:pk>/edit/', views.spot_edit, name='spot_edit'),
    path('spots/<int:pk>/delete/', views.spot_delete, name='spot_delete'),
]
