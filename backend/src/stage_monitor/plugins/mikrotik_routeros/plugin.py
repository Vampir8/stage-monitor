from stage_monitor.plugins.mikrotik_crs326.plugin import CRS326Plugin


class RouterOSPlugin(CRS326Plugin):
    """Basic read-only telemetry shared across RouterOS 7 hardware models."""
    board_prefix = None
    interface_types = ('ether', 'wlan', 'wifi', 'lte')

    async def get_state(self):
        state = await super().get_state()
        ports = state.extra['ports']
        available_sfp = {f'network.sfp{index}_status' for index in (1, 2)
                         if any(p['default_name'] in {f'sfp-sfpplus{index}', f'sfp{index}'} for p in ports)}
        state.metrics = [m for m in state.metrics if
                         not m.channel and
                         (not m.key.startswith('network.sfp') or m.key in available_sfp)
                         and (m.key != 'temperature' or m.value is not None)]
        for metric in state.metrics:
            if metric.key == 'network.ports_summary':
                metric.value = f"{sum(p['up'] is True for p in ports)}/{len(ports)}" if ports and all(p['up'] is not None for p in ports) else None
                metric.quality = 'unknown' if metric.value is None else metric.quality
        state.extra['traffic_scope'] = 'sum of Ethernet/SFP, wireless and LTE base interfaces; not unique end-to-end traffic'
        state.extra.pop('ports', None)
        return state
