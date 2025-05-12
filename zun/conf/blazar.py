from oslo_config import cfg

blazar_host_group = cfg.OptGroup(name='blazar:host')
# blazar_host_group = cfg.OptGroup(name='blazar:device')

blazar_host_opts = [
    cfg.BoolOpt('allow_without_reservation',
                default=False,
                help=('Whether to allow scheduling without '
                      'having a reservation. If True, when scheduling '
                      'a container without a reservation_id hint, '
                      'the container can be scheduled to a host as '
                      'long as the host is not explicitly reserved by '
                      'any tenant.'))
]


ALL_OPTS = (blazar_host_opts)



def register_opts(conf):
    conf.register_group(blazar_host_group)
    conf.register_opts(ALL_OPTS, blazar_host_group)

def list_opts():
    return {blazar_host_group: ALL_OPTS}
