#!/usr/bin/env python3

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlencode, quote


XPADDING_DEFAULT_BYTES = "100-1000"
XPADDING_DEFAULT_KEY = "x_padding"
XPADDING_DEFAULT_HEADER = "X-Padding"


def expand_json(value):
    """
    Amnezia хранит часть JSON как JSON-строки:
      Servers/serversList
      xray.last_config
      и т.д.

    Рекурсивно превращаем такие строки обратно в dict/list.
    """
    if isinstance(value, dict):
        return {k: expand_json(v) for k, v in value.items()}

    if isinstance(value, list):
        return [expand_json(v) for v in value]

    if isinstance(value, str):
        s = value.strip()

        if ((s.startswith("{") and s.endswith("}")) or
                (s.startswith("[") and s.endswith("]"))):
            try:
                return expand_json(json.loads(s))
            except json.JSONDecodeError:
                pass

    return value


def load_backup(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return expand_json(json.load(f))


def first(d, *keys, default=None):
    for key in keys:
        if isinstance(d, dict) and key in d and d[key] is not None:
            return d[key]
    return default


def to_bool(v):
    if isinstance(v, bool):
        return v

    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "on")

    return bool(v)


def make_range(lo, hi):
    if lo in (None, "") and hi in (None, ""):
        return None

    if lo in (None, ""):
        lo = hi

    if hi in (None, ""):
        hi = lo

    lo = str(lo)
    hi = str(hi)

    if lo == hi:
        return lo

    return f"{lo}-{hi}"


def normalize_mode(v):
    if not v:
        return "auto"

    return str(v).strip().lower()


def normalize_placement(v):
    s = str(v or "").strip()
    low = s.lower().replace(" ", "").replace("_", "")

    if low == "queryinheader":
        return "queryInHeader"

    if low in ("path", "cookie", "header", "query"):
        return low

    # Совпадает с актуальной логикой Amnezia.
    if low == "body":
        return "queryInHeader"

    return s


def normalize_padding_method(v):
    s = str(v or "").strip().lower()

    if not s or s in ("repeat-x", "random", "zero"):
        return "repeat-x"

    if s == "tokenish":
        return "tokenish"

    return s


def deep_merge(base, override):
    """
    override имеет больший приоритет.
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if (isinstance(value, dict) and
                isinstance(result.get(key), dict)):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def amnezia_profile_xhttp(xhttp):
    """
    Преобразует сохранённый блок:

        xray.xhttp

    в Xray share-link representation:

        top = host/path/mode
        extra = {...}
    """

    if not isinstance(xhttp, dict):
        return {}, {}

    top = {}
    extra = {}

    # ------------------------------------------------------------
    # host / path / mode НЕ кладём в extra.
    # ------------------------------------------------------------

    host = first(xhttp, "xhttp_host", "host")
    path = first(xhttp, "xhttp_path", "path")
    mode = first(xhttp, "xhttp_mode", "mode")

    if host:
        top["host"] = str(host)

    if path:
        top["path"] = str(path)

    if mode:
        top["mode"] = normalize_mode(mode)

    # ------------------------------------------------------------
    # HTTP behaviour
    # ------------------------------------------------------------

    v = first(xhttp, "xhttp_disable_grpc", "noGRPCHeader")
    if v is not None:
        extra["noGRPCHeader"] = to_bool(v)

    v = first(xhttp, "xhttp_disable_sse", "noSSEHeader")
    if v is not None:
        extra["noSSEHeader"] = to_bool(v)

    v = first(xhttp, "xhttp_uplink_method", "uplinkHTTPMethod")
    if v:
        extra["uplinkHTTPMethod"] = str(v).upper()

    # ------------------------------------------------------------
    # Session / sequence
    # ------------------------------------------------------------

    v = first(
        xhttp,
        "xhttp_session_placement",
        "sessionIDPlacement",
        "sessionPlacement"
    )

    if v:
        extra["sessionIDPlacement"] = normalize_placement(v)

    v = first(xhttp, "xhttp_seq_placement", "seqPlacement")

    if v:
        extra["seqPlacement"] = normalize_placement(v)

    v = first(
        xhttp,
        "xhttp_uplink_data_placement",
        "uplinkDataPlacement"
    )

    if v:
        v = str(v).lower()

        # Старая Amnezia UI "Query" -> Xray "header".
        if v == "query":
            v = "header"

        extra["uplinkDataPlacement"] = v

    # ------------------------------------------------------------
    # Traffic shaping
    # ------------------------------------------------------------

    r = make_range(
        first(xhttp, "xhttp_sc_max_each_post_bytes_min"),
        first(xhttp, "xhttp_sc_max_each_post_bytes_max")
    )

    if r:
        extra["scMaxEachPostBytes"] = r

    r = make_range(
        first(xhttp, "xhttp_sc_min_posts_interval_ms_min"),
        first(xhttp, "xhttp_sc_min_posts_interval_ms_max")
    )

    if r:
        extra["scMinPostsIntervalMs"] = r

    r = make_range(
        first(xhttp, "xhttp_sc_stream_up_server_secs_min"),
        first(xhttp, "xhttp_sc_stream_up_server_secs_max")
    )

    if r:
        extra["scStreamUpServerSecs"] = r

    # uplinkChunkSize=0 Amnezia сама не записывает в Xray JSON,
    # поэтому 0 здесь намеренно не добавляем.
    chunk = first(xhttp, "xhttp_uplink_chunk_size")

    if chunk not in (None, "", "0", 0):
        extra["uplinkChunkSize"] = int(chunk)

    # ------------------------------------------------------------
    # XPadding
    # ------------------------------------------------------------

    padding = first(xhttp, "xPadding", default={}) or {}

    obfs = first(
        padding,
        "xpadding_obfs_mode",
        "xPaddingObfsMode"
    )

    if obfs is not None:
        extra["xPaddingObfsMode"] = to_bool(obfs)

    if to_bool(obfs):

        r = make_range(
            first(
                padding,
                "xpadding_bytes_min",
                "xPaddingBytesMin"
            ),
            first(
                padding,
                "xpadding_bytes_max",
                "xPaddingBytesMax"
            )
        )

        # Если диапазон не сохранён, Xray реально использует
        # effective default 100-1000.
        extra["xPaddingBytes"] = r or XPADDING_DEFAULT_BYTES

        extra["xPaddingKey"] = first(
            padding,
            "xpadding_key",
            "xPaddingKey",
            default=XPADDING_DEFAULT_KEY
        ) or XPADDING_DEFAULT_KEY

        extra["xPaddingHeader"] = first(
            padding,
            "xpadding_header",
            "xPaddingHeader",
            default=XPADDING_DEFAULT_HEADER
        ) or XPADDING_DEFAULT_HEADER

        extra["xPaddingPlacement"] = normalize_placement(
            first(
                padding,
                "xpadding_placement",
                "xPaddingPlacement",
                default="Cookie"
            )
        )

        extra["xPaddingMethod"] = normalize_padding_method(
            first(
                padding,
                "xpadding_method",
                "xPaddingMethod",
                default="Repeat-x"
            )
        )

    # ------------------------------------------------------------
    # XMUX из сохранённого профиля.
    # 0-0 не переносим: для Xray это фактически unset.
    # ------------------------------------------------------------

    xmux = first(xhttp, "xmux", default={}) or {}

    if to_bool(first(xmux, "xmux_enabled", "enabled", default=False)):

        out = {}

        mappings = (
            (
                "maxConcurrency",
                "xmux_max_concurrency_min",
                "xmux_max_concurrency_max"
            ),
            (
                "maxConnections",
                "xmux_max_connections_min",
                "xmux_max_connections_max"
            ),
            (
                "cMaxReuseTimes",
                "xmux_c_max_reuse_times_min",
                "xmux_c_max_reuse_times_max"
            ),
            (
                "hMaxRequestTimes",
                "xmux_h_max_request_times_min",
                "xmux_h_max_request_times_max"
            ),
            (
                "hMaxReusableSecs",
                "xmux_h_max_reusable_secs_min",
                "xmux_h_max_reusable_secs_max"
            ),
        )

        for dst, min_key, max_key in mappings:

            r = make_range(
                first(xmux, min_key),
                first(xmux, max_key)
            )

            if r not in (None, "0", "0-0"):
                out[dst] = r

        if out:
            extra["xmux"] = out

    return top, extra


def runtime_xhttp(outbound):
    """
    xhttpSettings прямо из рабочего xray.last_config.

    Это более сильный источник, чем xray.xhttp.
    """

    ss = outbound.get("streamSettings", {})
    xhttp = ss.get("xhttpSettings", {}) or {}

    top = {}
    extra = {}

    for key in ("host", "path", "mode"):

        if key in xhttp and xhttp[key] not in ("", None):

            if key == "mode":
                top[key] = normalize_mode(xhttp[key])
            else:
                top[key] = xhttp[key]

    # Если last_config уже содержит extra.
    native_extra = xhttp.get("extra")

    if isinstance(native_extra, str):
        try:
            native_extra = json.loads(native_extra)
        except json.JSONDecodeError:
            native_extra = None

    if isinstance(native_extra, dict):
        extra.update(native_extra)

    # Всё остальное по XHTTP sharing scheme идёт в extra.
    for key, value in xhttp.items():

        if key in ("host", "path", "mode", "extra"):
            continue

        extra[key] = copy.deepcopy(value)

    return top, extra


def build_xhttp(outbound, amnezia_profile, runtime_only=False):

    runtime_top, runtime_extra = runtime_xhttp(outbound)

    if runtime_only:

        top = runtime_top
        extra = runtime_extra

    else:

        profile_top, profile_extra = amnezia_profile_xhttp(
            amnezia_profile
        )

        # last_config имеет приоритет.
        top = deep_merge(profile_top, runtime_top)
        extra = deep_merge(profile_extra, runtime_extra)

    # Явные defaults удобнее для импортёров.
    top.setdefault("path", "/")
    top.setdefault("mode", "auto")

    return top, extra


def outbound_data(outbound):

    vnext = outbound["settings"]["vnext"][0]
    user = vnext["users"][0]

    return (
        str(vnext["address"]),
        int(vnext["port"]),
        user
    )


def make_vless_uri(
    outbound,
    name,
    xhttp_profile=None,
    runtime_only=False
):

    address, port, user = outbound_data(outbound)

    ss = outbound.get("streamSettings", {})
    rs = ss.get("realitySettings", {})

    network = ss.get("network", "tcp")
    security = ss.get("security", "none")

    params = []

    params.append((
        "encryption",
        user.get("encryption", "none")
    ))

    params.append(("type", network))

    if security != "none":
        params.append(("security", security))

    flow = user.get("flow")

    if flow:
        params.append(("flow", flow))

    # ------------------------------------------------------------
    # XHTTP
    # ------------------------------------------------------------

    if network == "xhttp":

        top, extra = build_xhttp(
            outbound,
            xhttp_profile,
            runtime_only
        )

        for key in ("host", "path", "mode"):

            if key in top:
                params.append((key, str(top[key])))

        if extra:

            params.append((
                "extra",
                json.dumps(
                    extra,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True
                )
            ))

    # ------------------------------------------------------------
    # REALITY
    # ------------------------------------------------------------

    if security == "reality":

        fp = rs.get("fingerprint")

        if fp:
            params.append(("fp", fp))

        pbk = rs.get("publicKey") or rs.get("password")

        if pbk:
            params.append(("pbk", pbk))

        # Сохраняем даже пустые значения из native config.
        if "serverName" in rs:
            params.append((
                "sni",
                rs.get("serverName", "")
            ))

        if "shortId" in rs:
            params.append((
                "sid",
                rs.get("shortId", "")
            ))

        if "spiderX" in rs:
            params.append((
                "spx",
                rs.get("spiderX", "")
            ))

    query = urlencode(
        params,
        quote_via=quote,
        safe=""
    )

    # IPv6
    host = address

    if ":" in address and not address.startswith("["):
        host = f"[{address}]"

    return (
        f"vless://{user['id']}@{host}:{port}"
        f"?{query}"
        f"#{quote(name, safe='')}"
    )


def find_chains(data):

    servers = data.get("Servers/serversList", [])

    if not isinstance(servers, list):
        raise RuntimeError("Servers/serversList не является массивом")

    result = []

    for server in servers:

        for container in server.get("containers", []):

            if container.get("container") != "amnezia-xray":
                continue

            xray = container.get("xray", {})

            config = expand_json(
                xray.get("last_config")
            )

            if not isinstance(config, dict):
                continue

            vless = [
                o for o in config.get("outbounds", [])
                if o.get("protocol") == "vless"
            ]

            by_tag = {
                o["tag"]: o
                for o in vless
                if o.get("tag")
            }

            for exit_out in vless:

                dialer_proxy = (
                    exit_out
                    .get("streamSettings", {})
                    .get("sockopt", {})
                    .get("dialerProxy")
                )

                if not dialer_proxy:
                    continue

                relay = by_tag.get(dialer_proxy)

                if relay is None:
                    continue

                result.append((
                    server,
                    xray,
                    relay,
                    exit_out
                ))

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "backup",
        type=Path,
        help="AmneziaVPN .backup"
    )

    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help=(
            "Не дополнять relay данными xray.xhttp; "
            "использовать только настоящий last_config"
        )
    )

    args = parser.parse_args()

    data = load_backup(args.backup)

    chains = find_chains(data)

    if not chains:
        raise RuntimeError(
            "Не найден VLESS chain с sockopt.dialerProxy"
        )

    for server, xray, relay, exit_out in chains:

        api = server.get("api_config", {})

        country = (
            api.get("server_country_name")
            or server.get("name")
            or "Amnezia"
        )

        relay_name = f"Amnezia {country} relay"
        exit_name = f"Amnezia {country} exit"

        profile = xray.get("xhttp", {})

        relay_uri = make_vless_uri(
            relay,
            relay_name,
            xhttp_profile=profile,
            runtime_only=args.runtime_only
        )

        exit_uri = make_vless_uri(
            exit_out,
            exit_name
        )

        top, extra = build_xhttp(
            relay,
            profile,
            args.runtime_only
        )

        print()
        print("========== RELAY ==========")
        print(relay_uri)

        print()
        print("XHTTP:")
        print(json.dumps(
            top,
            ensure_ascii=False,
            indent=2
        ))

        print()
        print("EXTRA:")
        print(json.dumps(
            extra,
            ensure_ascii=False,
            indent=2
        ))

        print()
        print("========== EXIT ==========")
        print(exit_uri)

        print()
        print(
            "После импорта в Shadowrocket:"
        )

        print(
            f"EXIT -> Proxy Pass -> {relay_name}"
        )


if __name__ == "__main__":
    main()