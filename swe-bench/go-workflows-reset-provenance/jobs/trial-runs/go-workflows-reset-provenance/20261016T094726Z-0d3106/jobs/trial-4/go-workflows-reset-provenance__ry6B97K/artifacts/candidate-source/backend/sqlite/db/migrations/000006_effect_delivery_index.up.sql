-- Work items are looked up by the identity of the delivery they were published with.
CREATE INDEX IF NOT EXISTS idx_effect_delivery ON workflow_effects(instance_id, kind, json_extract(publication, '$.Delivery.id'));
