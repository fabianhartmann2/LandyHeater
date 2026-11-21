/* Fixed-purpose Bonjour publication for the Landy Heater Web UI.
 *
 * The module deliberately exposes no caller-controlled service names, ports,
 * hostnames or TXT data.  Python can only enable or disable the one audited
 * HTTP service after the existing WLAN port reports station mDNS ready.
 */

#include "py/runtime.h"

#include "esp_err.h"
#include "mdns.h"

#define LANDY_MDNS_INSTANCE "Landy Heater"
#define LANDY_MDNS_SERVICE "_http"
#define LANDY_MDNS_PROTO "_tcp"
#define LANDY_MDNS_PORT (80)

static mp_obj_t landy_mdns_advertise_http(void) {
    mdns_txt_item_t txt[] = {
        {"path", "/"},
    };
    esp_err_t result = mdns_service_add(
        LANDY_MDNS_INSTANCE,
        LANDY_MDNS_SERVICE,
        LANDY_MDNS_PROTO,
        LANDY_MDNS_PORT,
        txt,
        MP_ARRAY_SIZE(txt)
    );
    if (result == ESP_OK) {
        return mp_const_true;
    }
    /* INVALID_ARG also covers a missing hostname.  Only suppress it when the
     * exact fixed service is actually present; otherwise keep the broken mDNS
     * state visible to the Python owner instead of claiming publication.
     * mdns_service_add() returns INVALID_STATE when the server itself is not
     * initialised, so the existence query is safe on this branch.
     */
    if (
        result == ESP_ERR_INVALID_ARG
        && mdns_service_exists_with_instance(
            LANDY_MDNS_INSTANCE,
            LANDY_MDNS_SERVICE,
            LANDY_MDNS_PROTO,
            NULL
        )
    ) {
        return mp_const_false;
    }
    mp_raise_OSError(result);
}
static MP_DEFINE_CONST_FUN_OBJ_0(
    landy_mdns_advertise_http_obj,
    landy_mdns_advertise_http
);

static mp_obj_t landy_mdns_remove_http(void) {
    esp_err_t result = mdns_service_remove_for_host(
        LANDY_MDNS_INSTANCE,
        LANDY_MDNS_SERVICE,
        LANDY_MDNS_PROTO,
        NULL
    );
    if (result == ESP_OK) {
        return mp_const_true;
    }
    if (result == ESP_ERR_NOT_FOUND || result == ESP_ERR_INVALID_ARG) {
        return mp_const_false;
    }
    mp_raise_OSError(result);
}
static MP_DEFINE_CONST_FUN_OBJ_0(
    landy_mdns_remove_http_obj,
    landy_mdns_remove_http
);

static const mp_rom_map_elem_t landy_mdns_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_landy_mdns) },
    {
        MP_ROM_QSTR(MP_QSTR_advertise_http),
        MP_ROM_PTR(&landy_mdns_advertise_http_obj)
    },
    {
        MP_ROM_QSTR(MP_QSTR_remove_http),
        MP_ROM_PTR(&landy_mdns_remove_http_obj)
    },
};
static MP_DEFINE_CONST_DICT(
    landy_mdns_module_globals,
    landy_mdns_module_globals_table
);

const mp_obj_module_t landy_mdns_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&landy_mdns_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_landy_mdns, landy_mdns_user_cmodule);
