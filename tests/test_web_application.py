import unittest

from app.rest_application import RestResponse
from app.web_application import Phase9WebApplication
from app.web_assets import ASSET_VERSION, VERSIONED_ASSET_PREFIX
from services.http_protocol import encode_bytes_response, parse_request
from services.rest_security import INGRESS_ACCESS_POINT, RestSecurityPolicy
from tools.build_web_assets import OUTPUT, render


HOST = "192.168.4.1"
PEER = "192.168.4.2"
VERSIONED_ASSET_PREFIX_BYTES = VERSIONED_ASSET_PREFIX.encode("ascii")


def versioned_asset_url(name):
    return VERSIONED_ASSET_PREFIX_BYTES + name.encode("ascii")


def request(method="GET", target="/", host=HOST):
    framing = "Content-Length: 0\r\n" if method in ("POST", "PUT", "PATCH") else ""
    return parse_request(
        ("{} {} HTTP/1.1\r\nHost: {}\r\n{}\r\n".format(
            method, target, host, framing
        )).encode("ascii")
    )


class FakeRestRuntime:
    def __init__(self):
        self.security_policy = RestSecurityPolicy(
            lambda count: b"r" * count,
            (HOST,),
            INGRESS_ACCESS_POINT,
        )
        self.calls = []

    def handle(self, value, peer_ip):
        self.calls.append((value, peer_ip))
        return RestResponse(200, {"delegated": True})


class TestPhase9WebApplication(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRestRuntime()
        self.app = Phase9WebApplication(self.runtime)

    def assertOnlyVersionedAssetReferences(self, *payloads):
        references = 0
        for payload in payloads:
            self.assertNotIn(b"__LANDY_ASSET_VERSION__", payload)
            offset = 0
            while True:
                offset = payload.find(b"/assets/", offset)
                if offset < 0:
                    break
                references += 1
                self.assertTrue(
                    payload.startswith(VERSIONED_ASSET_PREFIX_BYTES, offset),
                    payload[offset:offset + 112],
                )
                offset += len(b"/assets/")
        self.assertGreater(references, 0)

    def test_root_and_every_allowlisted_asset_are_frozen_and_hardened(self):
        root = self.app.handle(request(), PEER)
        self.assertEqual(root.status, 200)
        self.assertEqual(root.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Landy Heater", root.body)
        self.assertIn("default-src 'self'", root.headers["Content-Security-Policy"])
        self.assertEqual(root.headers["X-Frame-Options"], "DENY")
        for path in (
            "/assets/boot.js",
            "/assets/ui.css",
            "/assets/diagnostics.html",
            "/assets/i18n.js",
            "/assets/app.js",
            "/assets/home.js",
            "/assets/timers.js",
            "/assets/settings.js",
            "/assets/setup.js",
            "/assets/diagnostics.js",
        ):
            with self.subTest(path=path):
                response = self.app.handle(request(target=path), PEER)
                self.assertEqual(response.status, 200)
                self.assertIs(type(response.body), bytes)
                self.assertLessEqual(len(response.body), 16 * 1024)
                self.assertIn("Content-Security-Policy", response.headers)

    def test_api_requests_are_delegated_without_rewriting(self):
        value = request(target="/api/v1/status")
        response = self.app.handle(value, PEER)
        self.assertEqual(response.body, {"delegated": True})
        self.assertEqual(self.runtime.calls, [(value, PEER)])

    def test_static_surface_is_closed_and_host_checked_first(self):
        cases = (
            (request(target="/missing"), 404),
            (request(method="POST"), 405),
            (request(target="/?x=1"), 400),
            (request(target="/?x=1", host="attacker.invalid"), 403),
        )
        for value, status in cases:
            with self.subTest(target=value.target, host=value.host):
                response = self.app.handle(value, PEER)
                self.assertEqual(response.status, status)
                self.assertIn("Content-Security-Policy", response.headers)

    def test_known_ap_captive_probes_redirect_but_station_does_not(self):
        for target in (
            "/generate_204",
            "/hotspot-detect.html",
            "/connecttest.txt",
            "/canonical.html",
        ):
            with self.subTest(target=target):
                response = self.app.handle(
                    request(target=target, host="probe.invalid"),
                    PEER,
                    "ap",
                    HOST,
                )
                self.assertEqual(response.status, 302)
                self.assertEqual(response.headers["Location"], "http://192.168.4.1/")
                encoded = encode_bytes_response(
                    response.status,
                    response.body,
                    response.content_type,
                    response.headers,
                )
                self.assertEqual(encoded.count(b"Cache-Control: no-store\r\n"), 1)
                station = self.app.handle(
                    request(target=target, host="probe.invalid"),
                    "10.0.0.2",
                    "sta",
                    "10.0.0.17",
                )
                self.assertEqual(station.status, 403)

        query_probe = self.app.handle(
            request(target="/generate_204?nonce=123", host="probe.invalid"),
            PEER,
            "ap",
            HOST,
        )
        self.assertEqual(query_probe.status, 302)
        self.assertEqual(query_probe.headers["Location"], "http://192.168.4.1/")

    def test_disabled_captive_portal_keeps_direct_ui_but_never_redirects(self):
        app = Phase9WebApplication(
            self.runtime, captive_portal_enabled=False
        )
        direct = app.handle(request(), PEER, "ap", HOST)
        self.assertEqual(direct.status, 200)
        probe = app.handle(
            request(target="/generate_204", host=HOST), PEER, "ap", HOST
        )
        self.assertEqual(probe.status, 404)
        self.assertNotIn("Location", probe.headers)

    def test_captive_portal_setting_requires_a_real_boolean(self):
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Phase9WebApplication(
                    self.runtime, captive_portal_enabled=value
                )

    def test_station_reads_accept_destination_ip_and_api_context(self):
        class IngressRuntime(FakeRestRuntime):
            def handle(self, value, peer_ip, ingress=None, local_ip=None):
                self.calls.append((value, peer_ip, ingress, local_ip))
                return RestResponse(200, {"delegated": True})

        runtime = IngressRuntime()
        app = Phase9WebApplication(runtime)
        static = app.handle(
            request(host="10.0.0.17"), "10.0.0.2", "sta", "10.0.0.17"
        )
        self.assertEqual(static.status, 200)
        api_request = request(target="/api/v1/status", host="10.0.0.17")
        api = app.handle(api_request, "10.0.0.2", "sta", "10.0.0.17")
        self.assertEqual(api.status, 200)
        self.assertEqual(runtime.calls[-1][2:], ("sta", "10.0.0.17"))

    def test_frontend_boots_reads_without_requesting_mutation_token(self):
        index = self.app.handle(request(), PEER).body
        loader = self.app.handle(request(target="/assets/boot.js"), PEER).body
        app = self.app.handle(request(target="/assets/app.js"), PEER).body
        boot = app.split(b"async function boot()", 1)[1]
        boot_url = versioned_asset_url("boot.js")
        css_url = versioned_asset_url("ui.css")
        i18n_url = versioned_asset_url("i18n.js")
        app_url = versioned_asset_url("app.js")
        home_status = (
            b'await Promise.all([L.loadAndBind("home"),L.loadStatus()])'
        )
        self.assertEqual(
            VERSIONED_ASSET_PREFIX,
            "/assets/v-{}/".format(ASSET_VERSION),
        )
        self.assertEqual(len(ASSET_VERSION), 64)
        self.assertIn(home_status, boot)
        self.assertLess(
            boot.index(home_status),
            boot.index(b"L.modules.home?.render(L.state.status)"),
        )
        self.assertNotIn(b"await L.security();await L.loadStatus()", boot)
        self.assertEqual(index.count(b'<link rel="stylesheet"'), 0)
        self.assertEqual(index.count(b"<script defer"), 1)
        self.assertIn(b'src="' + boot_url + b'"', index)
        self.assertNotIn(b'src="' + app_url + b'"', index)
        self.assertIn(b'load("link"', loader)
        self.assertIn(b'await load("script"', loader)
        parallel_start = loader.index(b"await Promise.all([")
        parallel_end = loader.index(b"]);await load(", parallel_start)
        parallel_loads = loader[parallel_start:parallel_end]
        self.assertIn(css_url, parallel_loads)
        self.assertIn(i18n_url, parallel_loads)
        self.assertNotIn(app_url, parallel_loads)
        self.assertIn(app_url, loader[parallel_end:])
        self.assertIn(b"attempt<3", loader)
        for name in (
            "home.js",
            "timers.js",
            "settings.js",
            "setup.js",
            "diagnostics.js",
        ):
            self.assertIn(b'"' + versioned_asset_url(name) + b'"', app)
        self.assertOnlyVersionedAssetReferences(index, loader, app)
        self.assertNotIn(b'for(const name of ["home","timers","settings"])', app)
        self.assertIn(b'if(name==="timers")', app)
        self.assertIn(b'L.loadAndBind("timers")', app)
        self.assertIn(b'if(name==="settings")', app)
        self.assertIn(b'L.loadAndBind("settings")', app)
        self.assertIn(b'if(name==="diagnostics")', app)
        self.assertIn(b'L.loadAndBind("diagnostics")', app)
        self.assertIn(b'L.state.view==="settings"', app)
        self.assertIn(b'L.state.view==="timers"', app)
        self.assertIn(b'await L.showView(initial)', boot)
        self.assertIn(
            b'L.state.status?.configuration?.setup_complete===false', boot
        )
        self.assertNotIn(
            b'L.state.settings?.system?.setup_complete===false', boot
        )
        self.assertNotIn(b"Promise.all([L.loadStatus()", app)
        self.assertIn(b'document.readyState==="loading"', app)

    def test_lazy_views_load_only_their_required_state(self):
        app = self.app.handle(request(target="/assets/app.js"), PEER).body
        home = self.app.handle(request(target="/assets/home.js"), PEER).body
        boot = app.split(b"async function boot()", 1)[1]
        self.assertIn(
            b'if(name==="timers"){const module=await L.loadAndBind("timers")',
            app,
        )
        self.assertIn(
            b'if(!L.state.settings)await L.loadSettings();'
            b'if(!L.state.timers)await L.loadTimers(0)',
            app,
        )
        self.assertIn(
            b'if(name==="settings"){const module=await L.loadAndBind("settings")',
            app,
        )
        self.assertIn(b'if(!moduleBindings[name])', app)
        self.assertIn(b'if(viewLoads[name])return viewLoads[name]', app)
        self.assertIn(b'const requested=++viewRequest', app)
        self.assertIn(b'if(requested!==viewRequest)return', app)
        self.assertIn(b'const initialViewRequest=viewRequest', app)
        self.assertIn(
            b'if(viewRequest===initialViewRequest)await L.showView(initial)',
            app,
        )
        self.assertLess(
            boot.index(b'const initialViewRequest=viewRequest'),
            boot.index(
                b'await Promise.all([L.loadAndBind("home"),L.loadStatus()])'
            ),
        )
        self.assertIn(b'else module.render(L.state.settings)', app)
        self.assertIn(b'module.renderStatus(L.state.status)', app)
        self.assertIn(
            b'else if(L.state.view==="timers"){await L.loadSettings();'
            b'await L.loadTimers(',
            app,
        )
        self.assertNotIn(
            b'else if(L.state.view==="timers"){if(!L.state.settings)',
            app,
        )
        self.assertLess(
            app.index(b"await L.ensureView(name)"),
            app.index(b"L.state.view=name"),
        )
        self.assertIn(b'let startDialogLoad=null', home)
        self.assertIn(
            b'if(startDialogLoad)return startDialogLoad',
            home,
        )
        self.assertIn(
            b'if(!L.state.settings)await L.loadSettings();'
            b'prepareStartDialog()',
            home,
        )
        self.assertIn(b'finally{startDialogLoad=null}', home)

    def test_browser_time_sync_is_utc_bounded_and_visible(self):
        index = self.app.handle(request(), PEER).body
        app = self.app.handle(request(target="/assets/app.js"), PEER).body
        settings = self.app.handle(
            request(target="/assets/settings.js"), PEER
        ).body
        self.assertIn(b'id="sync-browser-time"', index)
        self.assertIn(b'id="setup-sync-browser-time"', index)
        self.assertIn(b"Math.floor(Date.now()/1000)", app)
        self.assertIn(b'"/api/v1/time/browser-sync"', app)
        self.assertIn(b"unix_epoch_seconds", app)
        self.assertIn(b"L.state.status?.time?.valid!==true", app)
        self.assertIn(b"volatile_browser_time", settings)
        self.assertIn(b"timer_trusted", settings)

    def test_network_service_switches_are_bounded_and_restart_explicit(self):
        index = self.app.handle(request(), PEER).body
        settings = self.app.handle(
            request(target="/assets/settings.js"), PEER
        ).body
        self.assertIn(b'id="network-services-settings"', index)
        self.assertIn(b'id="setting-captive-portal" type="checkbox"', index)
        self.assertIn(b'id="setting-bonjour-http" type="checkbox"', index)
        self.assertIn(
            b'network_services={captive_portal_enabled:', settings
        )
        self.assertIn(b'bonjour_http_enabled:', settings)
        self.assertIn(
            b'L.mutate("/api/v1/settings","PATCH",{network_services})',
            settings,
        )
        self.assertIn(b'savedRestartRequired', settings)
        self.assertIn(b'captivePortalService', settings)
        self.assertIn(b'bonjourHttpService', settings)
        self.assertIn(b'networkServicesHint', settings)
        self.assertIn(b'L.applyLanguage(L.state.language)', settings)

    def test_start_failures_are_localized_without_hiding_safe_start_blocks(self):
        home = self.app.handle(
            request(target="/assets/home.js"), PEER
        ).body
        self.assertIn(b'error.code==="runtime_restart_required"', home)
        self.assertIn(b'error.code!=="heater_start_conflict"', home)
        self.assertIn(b'actual.communication==="error"', home)
        self.assertIn(b'actual.initialized!==true', home)
        self.assertIn(b'actual.synchronized!==true', home)
        self.assertIn(b'sensor.health!=="ok"', home)
        self.assertIn(b'startMessage("restart")', home)
        self.assertIn(b'startMessage("unreachable")', home)
        self.assertIn(b'startMessage("notReady")', home)
        self.assertIn(b'startMessage("blocked")', home)
        self.assertIn(
            "Neustart erforderlich. Starte das Steuergerät neu".encode(),
            home,
        )
        self.assertIn(
            "Heizung nicht erreichbar. Prüfe Stromversorgung".encode(),
            home,
        )
        self.assertEqual(home.count(b"error=>startFailure(error"), 2)

    def test_home_uses_station_capable_control_token_only_for_heater_actions(self):
        app = self.app.handle(
            request(target="/assets/app.js"), PEER
        ).body
        home = self.app.handle(
            request(target="/assets/home.js"), PEER
        ).body
        settings = self.app.handle(
            request(target="/assets/settings.js"), PEER
        ).body
        self.assertIn(b'controlCsrf:null', app)
        self.assertIn(b'"/api/v1/control-context"', app)
        self.assertIn(b'L.controlMutate=async', app)
        self.assertEqual(home.count(b"L.controlMutate("), 5)
        self.assertNotIn(b'L.mutate("/api/v1/heater/', home)
        self.assertIn(b'L.mutate("/api/v1/settings"', settings)
        self.assertIn(
            b'error.code!=="mutation_security_unavailable"', app
        )

    def test_frontend_has_no_external_dependency_or_inline_executable_code(self):
        index = self.app.handle(request(), PEER).body
        self.assertNotIn(b"http://", index)
        self.assertNotIn(b"https://", index)
        self.assertNotIn(b"<style", index.lower())
        self.assertNotIn(b"<script>", index.lower())
        self.assertIn(b'data-i18n="home"', index)
        self.assertIn(b'prefers-color-scheme', self.app.handle(
            request(target="/assets/ui.css"), PEER
        ).body)

    def test_generated_frozen_assets_match_the_readable_sources_exactly(self):
        self.assertEqual(OUTPUT.read_bytes(), render())

    def test_setup_assistant_is_local_bounded_and_uses_the_setup_api(self):
        index = self.app.handle(request(), PEER).body
        setup = self.app.handle(
            request(target="/assets/setup.js"), PEER
        ).body
        self.assertIn(b'id="setup-dialog"', index)
        self.assertEqual(index.count(b"data-setup-step="), 9)
        self.assertIn(b'"/api/v1/setup"', setup)
        self.assertIn(b'password_action', setup)
        self.assertIn(b'id="setup-ap-action"', index)
        self.assertIn(b'setup-network-action', setup)
        self.assertIn(b'networksError', setup)
        self.assertIn(b'apError', setup)
        self.assertIn(b'quickError', setup)
        self.assertIn(b'sensorError', setup)
        self.assertIn(b'validateAll', setup)
        self.assertIn(b'"deferred"', setup)
        self.assertIn(b'id="setup-sensor-live"', index)
        self.assertIn(b'id="setup-sensor-skip"', index)
        self.assertIn(b'setTimeout(refreshSensorData,1500)', setup)
        self.assertIn(b'L.request("/api/v1/setup")', setup)
        self.assertNotIn(b'src="/assets/setup.js"', index)
        app = self.app.handle(request(target="/assets/app.js"), PEER).body
        self.assertIn(b'loadAndBind("setup")', app)
        self.assertIn(b'L.openSetup(false)', app)
        self.assertNotIn(b"localStorage", setup)
        self.assertNotIn(b"http://", setup)
        self.assertNotIn(b"https://", setup)

    def test_bodyless_mutations_omit_the_fetch_body_member(self):
        app = self.app.handle(
            request(target="/assets/app.js"), PEER
        ).body
        self.assertIn(b"options={method,headers}", app)
        self.assertIn(b"options.body=JSON.stringify(payload)", app)
        self.assertIn(b"L.request(path,options)", app)
        self.assertNotIn(b'let body=""', app)
        self.assertNotIn(b"{method,headers,body}", app)

    def test_dialog_cancel_buttons_cannot_submit_heater_or_timer_forms(self):
        index = self.app.handle(request(), PEER).body
        home = self.app.handle(request(target="/assets/home.js"), PEER).body
        timers = self.app.handle(request(target="/assets/timers.js"), PEER).body
        self.assertEqual(index.count(b'type="button" data-start-cancel'), 2)
        self.assertEqual(index.count(b'type="button" data-timer-cancel'), 2)
        self.assertIn(b'[data-start-cancel]', home)
        self.assertIn(b'event.submitter?.value==="cancel"', home)
        self.assertIn(b'[data-timer-cancel]', timers)
        self.assertIn(b'event.submitter?.value==="cancel"', timers)

    def test_phase11_diagnostics_surface_is_local_bounded_and_safe(self):
        index = self.app.handle(request(), PEER).body
        surface = self.app.handle(
            request(target="/assets/diagnostics.html"), PEER
        ).body
        script = self.app.handle(
            request(target="/assets/diagnostics.js"), PEER
        ).body
        self.assertIn(b'data-view="diagnostics"', index)
        self.assertIn(b'id="view-diagnostics"', surface)
        self.assertIn(b"/api/v1/events?", script)
        self.assertIn(b'"/api/v1/capture"', script)
        self.assertIn(b"/api/v1/capture/export?", script)
        self.assertIn(b"/api/v1/diagnostics?event_after=", script)
        self.assertIn(b"setTimeout(resolve,1300)", script)
        self.assertIn(b"application/x-ndjson", script)
        self.assertIn(b"async function hydrate()", script)
        diagnostics_url = versioned_asset_url("diagnostics.html")
        self.assertIn(b'fetch("' + diagnostics_url + b'")', script)
        self.assertNotIn(b'{cache:"no-cache"}', script)
        self.assertOnlyVersionedAssetReferences(script)
        self.assertIn(b'if(name==="diagnostics"&&!state.hydrated)', script)
        self.assertIn(b"setInterval(load,2000)", script)
        self.assertIn(b"textContent", script)
        self.assertNotIn(b"innerHTML", script)
        self.assertNotIn(b"https://", surface + script)
        self.assertNotIn(b"http://", surface + script)


if __name__ == "__main__":
    unittest.main()
