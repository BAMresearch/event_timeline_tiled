# Grafana with pinned plugins installed at build time. At start-up nothing is
# downloaded: default plugin preinstallation, auto-update and update checks are off.
FROM grafana/grafana:13.3.1@sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf

ENV GF_PATHS_PLUGINS=/opt/grafana/plugins \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_AUTH_ANONYMOUS_ENABLED=false

USER root
RUN mkdir -p /opt/grafana/plugins \
 && grafana cli --pluginsDir /opt/grafana/plugins plugins install yesoreyeram-infinity-datasource 4.1.6 \
 && grafana cli --pluginsDir /opt/grafana/plugins plugins install volkovlabs-variable-panel 5.2.0 \
 && chown -R grafana:root /opt/grafana/plugins
USER grafana
