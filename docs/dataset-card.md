# Dataset Card: Kiosk Evaluation Set

## Status

No participant data is included in this repository. This document defines the intended consented, project-collected evaluation set; it does not assert that collection or validation has occurred.

## Intended use

Measure the prototype's presentation-attack classification, identity matching, verification rate, and decision latency under the conditions recorded in the protocol. It is not intended for deployment as a standalone access-control benchmark or for identifying people outside the study.

## Composition to record

For each approved session, document counts by split, truth (genuine/attack), attack type, lighting condition, collection device, and exclusions. Track unique participants and attempts separately. Keep the identity mapping and raw media in restricted storage, not in this repository.

## Collection and limitations

Participants must provide documented informed consent. Splits must be subject-disjoint. The sample will be small and local to the team's capture environment, so results may not generalize across populations, devices, locations, masks, or attack materials. Mask and injection performance may be particularly uncertain. Report subgroup denominators and confidence intervals where collection governance permits; do not claim fairness or broad security from aggregate results alone.

## Privacy and maintenance

Collect minimum necessary data, restrict and encrypt storage, define a deletion date before collection, and publish only aggregate metrics. Record dataset version, protocol changes, exclusions, and deletion completion here without recording personal data.