from django.db import models

# Create your models here.

class Message(models.Model):
    description = models.TextField()