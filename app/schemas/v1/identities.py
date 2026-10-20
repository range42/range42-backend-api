"""Provider-neutral desired identity contract; observed provider state is read-only."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
PersonId = Annotated[str, Field(pattern=r'^person-[a-f0-9]{32}$')]
RegistryId = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RegisterScope(Strict):
    type: Literal['scope.register']


class RegisterPerson(Strict):
    type: Literal['person.register']
    record_ref: Key
    kind: Literal['temporary', 'permanent']
    person_id: PersonId | None = None


class SetMembership(Strict):
    type: Literal['membership.set']
    person_id: PersonId
    role: Literal['participant', 'facilitator', 'director', 'observer']
    team_ref: Key | None = None


class RemoveMembership(Strict):
    type: Literal['membership.remove']
    person_id: PersonId


class IdentityAction(Strict):
    registry_id: RegistryId
    expected_revision: Annotated[int, Field(ge=0)]
    run_revision: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    operation: Annotated[RegisterScope | RegisterPerson | SetMembership | RemoveMembership, Field(discriminator='type')]


class Person(BaseModel):
    id: PersonId
    kind: Literal['temporary', 'permanent']
    enabled: bool
    generation: int
    reconcile_state: Literal['unconfigured', 'pending', 'partial', 'failed', 'synchronized']


class Scope(BaseModel):
    ref: Key
    expires_at: str
    participant_limit_per_team: Annotated[int, Field(ge=1, le=1000)] | None = None


class SourceReference(BaseModel):
    scope_ref: Key
    record_ref: Key
    person_id: PersonId


class Provider(BaseModel):
    id: Key
    kind: Literal['oidc', 'vpn', 'application', 'local']
    authority: str
    instance_ref: Key


class Binding(BaseModel):
    person_id: PersonId
    provider_id: Key
    subject: str


class Membership(BaseModel):
    person_id: PersonId
    scope_ref: Key
    role: Literal['participant', 'facilitator', 'director', 'observer']
    team_ref: Key | None
    active: bool
    effective: bool


class Job(Binding):
    id: str
    generation: int
    state: Literal['queued', 'failed', 'unsupported', 'succeeded', 'superseded']
    error_code: Literal['REMOTE_RECONCILE_FAILED', 'ADAPTER_CAPABILITY_UNSUPPORTED'] | None


class IdentityState(BaseModel):
    registry_id: RegistryId
    revision: Annotated[int, Field(ge=0)]
    people: list[Person]
    scopes: list[Scope]
    source_references: list[SourceReference]
    providers: list[Provider]
    bindings: list[Binding]
    memberships: list[Membership]
    jobs: list[Job]


class RunIdentities(BaseModel):
    deployment_id: str
    expires_at: str
    teams: list[str]
    participants_per_environment: Annotated[int, Field(ge=1, le=10)] = 1
    run_revision: str
    state: IdentityState


class IdentityIntegrationCapabilities(Strict):
    optional: Literal[True] = True
    configuration_state: Literal['not_configured', 'configured', 'invalid_configuration']
    provider_access_verified: Literal[False] = False
    required_for_environment_deployment: Literal[False] = False


class IdentityCapabilities(IdentityIntegrationCapabilities):
    deployment_id: str
