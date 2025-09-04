from django.views.generic import CreateView
from django.contrib.auth.mixins import LoginRequiredMixin

from .forms import MessageForm
from .models import Message

# Create your views here.

class AddMessageView(LoginRequiredMixin, CreateView):
    form_class = MessageForm

def message_request_user_view(request):

    user = request.user
    review = Message.objects.filter(user=user)
#   return render(request, template_name, {'review': review})