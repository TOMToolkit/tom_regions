from django.urls import path

from tom_regions.views import DemoView, ProfileUpdateView


app_name = 'tom_regions'

urlpatterns = [
    path('<int:pk>/demo', DemoView.as_view(), name='demo-page'),
    path('', DemoView.as_view(), name='demo-page'),
    path('users/<int:pk>/update/', ProfileUpdateView.as_view(), name='demo-profile-update'),
]
