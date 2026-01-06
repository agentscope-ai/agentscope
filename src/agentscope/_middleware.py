# -*- coding: utf-8 -*-
""""""


class AgentMiddlewareBase:

    def pre_reasoning_hook(self):
        raise NotImplementedError()

    def post_reasoning_hook(self):
        raise NotImplementedError()

    def pre_acting_hook(self):
        raise NotImplementedError()

    def post_acting_hook(self):
        raise NotImplementedError()

    def pre_reply_hook(self):
        raise NotImplementedError()

    def post_reply_hook(self):
        raise NotImplementedError()

    def pre_observe_hook(self):
        raise NotImplementedError()

    def post_observe_hook(self):
        raise NotImplementedError()

    def reasoning_wrapper(self):
        raise NotImplementedError()

    def acting_wrapper(self):
        raise NotImplementedError()

    def reply_wrapper(self):
        raise NotImplementedError()

    def print_wrapper(self):
        raise NotImplementedError()

    def observe_wrapper(self):
        raise NotImplementedError()



