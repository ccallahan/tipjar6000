import reflex as rx
import os

class ReflextemplateConfig(rx.Config):
    pass

config = ReflextemplateConfig(
    app_name="adampos",
    plugins=[rx.plugins.TailwindV3Plugin()],
    telemetry_enabled=False,
    frontend_port=3000, # default frontend port
    backend_port=8000, # default backend port
    # use https and the railway public domain with a backend route if available, otherwise default to a local address
    api_url='https://tipjar6000-testing.up.railway.app/backend'
)