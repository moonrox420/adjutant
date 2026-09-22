SET search_path=adjutant,public;

ALTER TABLE studio_job ADD COLUMN concept_count integer NOT NULL DEFAULT 1
    CHECK (concept_count IN (1,5));
ALTER TABLE studio_job ADD UNIQUE (brand_id,id);
ALTER TABLE studio_draft ADD COLUMN job_id uuid;
ALTER TABLE studio_draft ADD COLUMN concept_index integer CHECK (concept_index BETWEEN 0 AND 4);
ALTER TABLE studio_draft ADD FOREIGN KEY (brand_id,job_id) REFERENCES studio_job(brand_id,id);
ALTER TABLE studio_draft ADD CHECK ((job_id IS NULL)=(concept_index IS NULL));
ALTER TABLE studio_draft ADD UNIQUE (job_id,concept_index);
UPDATE studio_draft d SET job_id=j.id,concept_index=0 FROM studio_job j WHERE d.id=j.id;

CREATE FUNCTION validate_studio_concept_set() RETURNS trigger
LANGUAGE plpgsql SET search_path=adjutant,pg_temp AS $$
BEGIN
    IF NEW.state='completed' AND (
        SELECT count(*) FROM studio_draft d WHERE d.job_id=NEW.id AND d.state='completed'
    )<>NEW.concept_count THEN
        RAISE EXCEPTION 'Every requested concept must complete before its Studio job';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION validate_studio_concept_set() FROM PUBLIC;
CREATE TRIGGER studio_concept_completion BEFORE UPDATE OF state ON studio_job
FOR EACH ROW EXECUTE FUNCTION validate_studio_concept_set();
