from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from trmm_mcp.settings import Settings as TrmmMcpSettings


class BridgeSettings(BaseSettings):
    """Environment-driven configuration for approval-bridge.

    Composes `trmm_mcp.settings.Settings` (shared Redis/Postgres/TRMM/policy
    config) under `.trmm` and adds three bridge-specific fields: the bearer
    secret nanoclaw must present, plus listen host/port.

    Composition (not subclassing) keeps the bridge's settings surface tight:
    only ``api_key``, ``host``, ``port`` are bridge-owned, with everything
    else delegated unchanged to trmm-mcp's existing pydantic model.
    """

    model_config = SettingsConfigDict(extra="ignore")

    api_key: str = Field(alias="NANORMM_BRIDGE_API_KEY")
    host: str = Field(default="0.0.0.0", alias="NANORMM_BRIDGE_HOST")  # noqa: S104
    port: int = Field(default=8000, alias="NANORMM_BRIDGE_PORT")
    mcp_path: str = Field(default="/mcp", alias="NANORMM_MCP_PATH")

    @property
    def trmm(self) -> TrmmMcpSettings:
        """Lazily-constructed trmm-mcp settings. Reading this property is
        what triggers env var validation for the shared fields, so missing
        TRMM_API_BASE etc. surfaces as a normal pydantic error at startup.
        """
        if not hasattr(self, "_trmm"):
            object.__setattr__(self, "_trmm", TrmmMcpSettings())
        return self._trmm
