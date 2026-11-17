"""Explicit SYNTHETIC configuration: known local capability categories only.

No live resource verification, actual project, file path, endpoint or engine call.
The two keys intentionally match the already reviewed domain adapter seam.
"""
from cyomics_projects.resource_selector import InputIdentity,ServerProjectContext,LocalResource,OfficialCapability

INPUT=InputIdentity('11111111-1111-4111-8111-111111111111',('SYNTHETIC_P1',),
    (('SYNTHETIC_P1','a'*64),),3702,'native_protein')
CONTEXT=ServerProjectContext('22222222-2222-4222-8222-222222222222',True,'2026-11-17T00:00:00Z')
JOB_ID='33333333-3333-4333-8333-333333333333'
LOCAL=(
    LocalResource('blast.protein_homology','swissprot-reviewed','usable','synthetic-swissprot-usable',
        (True,)*5,'any',mapping_method='sequence_homology',database_name='Swiss-Prot',engine='blastp',engine_version='2.17.0+'),
    LocalResource('pfam.domains','pfam-raw-reviewed','usable','synthetic-pfam-usable',
        (True,)*5,'any',mapping_method='hmmsearch',database_name='Pfam',engine='hmmsearch',engine_version='3.3.2',parameters_json='{"cut_ga":true}'),
    LocalResource('string.network','synthetic-string-network','unverified','synthetic-string-not-yet-checked'),
    LocalResource('string.id_mapping','synthetic-string-mapping','unverified','synthetic-string-mapping-not-checked'),
)
# These registrations simulate future verified official metadata for tests.
# They make no claim that this project has implemented or tested an API.
STRING_MAPPING=OfficialCapability('string.id_mapping','synthetic-string-official','synthetic-api-contract',
    True,True,True,'selected_ids',parameters_json='{"species":3702}')

def partial_string_local():
    return (
        LocalResource('string.network','synthetic-string-network','usable','synthetic-network-checked',
            (True,)*5,'exact',3702,'verified_string_ids','STRING'),
        LocalResource('string.id_mapping','synthetic-string-mapping','missing','synthetic-mapping-absent'),
    )
