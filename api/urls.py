from django.urls import path
from . import views

urlpatterns = [
    path("impacto/", views.impacto, name="impacto"),
    path("asteroides/", views.asteroides, name="asteroides"),
]