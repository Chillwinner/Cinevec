from dataclasses import dataclass

from app.application.dashboard import DashboardService
from app.application.import_service import ImportService
from app.application.job_service import JobService
from app.application.lifecycle_service import LifecycleService
from app.application.lexical_search import LexicalSearchService
from app.application.media_service import MediaService
from app.application.refresh_service import RefreshService
from app.application.search_service import SearchService
from app.application.settings_service import SettingsService
from app.application.source_service import SourceService
from app.application.status_service import StatusService
from app.application.subtitle_service import SubtitleService
from app.application.tag_service import TagService
from app.application.upload_service import UploadService
from app.application.auth_service import AuthService
from app.application.api_token_service import ApiTokenService
from app.infra.auth import DefaultAuthorizationPolicy
from app.application.rebuild_service import RebuildService
from app.infra.config import Settings


@dataclass
class Container:
    settings: Settings
    sources: SourceService
    imports: ImportService
    jobs: JobService
    search: SearchService
    status: StatusService
    app_settings: SettingsService
    subtitles: SubtitleService
    refresh: RefreshService
    lifecycle: LifecycleService
    auth: AuthService
    policy: DefaultAuthorizationPolicy
    rebuild: RebuildService | None = None
    tags: TagService | None = None
    uploads: UploadService | None = None
    lexical: LexicalSearchService | None = None
    dashboard: DashboardService | None = None
    media: MediaService | None = None
    api_tokens: ApiTokenService | None = None
