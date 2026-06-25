USE StuckIn
GO

/****** Object:  View [dbo].[UserSkillsView] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

CREATE VIEW [dbo].[UserSkillsView]
AS
SELECT
    UTM.UserId,
    T.TopicId AS TopicId,
    T.Name AS SkillName,
    UTM.Mastery AS Proficiency,
    UTM.Confidence AS Confidence,
    UTM.EvidenceCount AS EvidenceCount,
    UTM.LastUpdated AS LastUpdated
FROM dbo.UserTopicMasteries UTM
JOIN dbo.Topics T ON UTM.TopicId = T.TopicId;
GO

/****** Object:  StoredProcedure [dbo].[usp_ComputeEvidenceScore] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Aggregates Evidence records for a (SessionId, TopicId) into single score
CREATE PROCEDURE [dbo].[usp_ComputeEvidenceScore]
    @SessionId      BIGINT,
    @TopicId        INT,
    @EvidenceScore  DECIMAL(3,2) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @QuizScore      DECIMAL(5,4) = 0,
        @StudyScore     DECIMAL(5,4) = 0,
        @RetentionScore DECIMAL(5,4) = 0;

    -- quiz/assessment scores (average if multiple records)
    SELECT @QuizScore = ISNULL(AVG(CAST(Score AS DECIMAL(5,4))), 0)
    FROM   Evidence
    WHERE  SessionId = @SessionId
      AND  TopicId   = @TopicId
      AND  Type IN ('quiz', 'assessment');

    -- study_time score
    SELECT @StudyScore = ISNULL(AVG(CAST(Score AS DECIMAL(5,4))), 0)
    FROM   Evidence
    WHERE  SessionId = @SessionId
      AND  TopicId   = @TopicId
      AND  Type = 'study_time';

    -- retention_test score
    SELECT @RetentionScore = ISNULL(AVG(CAST(Score AS DECIMAL(5,4))), 0)
    FROM   Evidence
    WHERE  SessionId = @SessionId
      AND  TopicId   = @TopicId
      AND  Type = 'retention_test';

    -- Weighted composite (0.50 + 0.25 + 0.25 = 1.00)
    SET @EvidenceScore = ROUND(
        (0.50 * @QuizScore)
      + (0.25 * @StudyScore)
      + (0.25 * @RetentionScore),
    2);

    SELECT
        @SessionId      AS SessionId,
        @TopicId        AS TopicId,
        @QuizScore      AS QuizScore,
        @StudyScore     AS StudyScore,
        @RetentionScore AS RetentionScore,
        @EvidenceScore  AS EvidenceScore;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_UpdateMastery] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Updates mastery using EMA formula: NewMastery = OldMastery*(1-α) + EvidenceScore*α, where α=0.20
CREATE PROCEDURE [dbo].[usp_UpdateMastery]
    @UserId        NVARCHAR(450),
    @TopicId       INT,
    @EvidenceScore DECIMAL(3,2)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Alpha      DECIMAL(3,2) = 0.20;
    DECLARE @OldMastery DECIMAL(3,2) = 0.00;
    DECLARE @NewMastery DECIMAL(3,2);

    -- Ensure UserTopicMastery record exists
    IF NOT EXISTS (
        SELECT 1 FROM UserTopicMasteries
        WHERE UserId = @UserId AND TopicId = @TopicId
    )
        INSERT INTO UserTopicMasteries (UserId, TopicId, Mastery, Confidence, Interest, EvidenceCount, LastUpdated)
        VALUES (@UserId, @TopicId, 0.00, 0.00, 0.50, 0, GETUTCDATE());

    -- Get old mastery value
    SELECT @OldMastery = Mastery
    FROM   UserTopicMasteries
    WHERE  UserId = @UserId AND TopicId = @TopicId;

    -- Calculate new mastery via EMA
    SET @NewMastery = ROUND((@OldMastery * (1 - @Alpha)) + (@EvidenceScore * @Alpha), 2);

    -- Update record
    UPDATE UserTopicMasteries
    SET
        Mastery       = @NewMastery,
        EvidenceCount = EvidenceCount + 1,
        LastUpdated   = GETUTCDATE()
    WHERE UserId = @UserId AND TopicId = @TopicId;

    SELECT
        @UserId        AS UserId,
        @TopicId       AS TopicId,
        @OldMastery    AS OldMastery,
        @EvidenceScore AS EvidenceScore,
        @NewMastery    AS NewMastery;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_RecalcDomainScore] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Calculates Domain score as weighted average of child topic masteries
CREATE PROCEDURE [dbo].[usp_RecalcDomainScore]
    @UserId        NVARCHAR(450),
    @DomainTopicId INT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @NewScore DECIMAL(3,2);

    -- Calculate weighted average of child topics
    SELECT @NewScore = ROUND(
        SUM(UTM.Mastery * TR.Weight) / NULLIF(SUM(TR.Weight), 0),
    2)
    FROM TopicRelationships TR
    JOIN UserTopicMasteries   UTM
        ON  UTM.UserId  = @UserId
        AND UTM.TopicId = TR.TargetTopicId
    WHERE TR.SourceTopicId    = @DomainTopicId
      AND TR.RelationshipType = 'contains';

    IF @NewScore IS NULL SET @NewScore = 0.00;

    -- Upsert UserDomains
    IF EXISTS (SELECT 1 FROM UserDomains WHERE UserId = @UserId AND TopicId = @DomainTopicId)
        UPDATE UserDomains SET Score = @NewScore
        WHERE UserId = @UserId AND TopicId = @DomainTopicId;
    ELSE
        INSERT INTO UserDomains (UserId, TopicId, Score)
        VALUES (@UserId, @DomainTopicId, @NewScore);

    SELECT @UserId AS UserId, @DomainTopicId AS DomainTopicId, @NewScore AS NewDomainScore;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_ComputeConfidenceScore] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Calculates confidence score using formula:
-- Confidence = 0.30*SessionScore + 0.30*AssessmentScore + 0.20*RecencyScore + 0.20*ConsistencyScore
CREATE PROCEDURE [dbo].[usp_ComputeConfidenceScore]
    @UserId  NVARCHAR(450),
    @TopicId INT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @SessionCount     INT,
        @AssessCount      INT,
        @DaysSince        INT,
        @StdDev           FLOAT,
        @AvgScore         FLOAT,
        @SessionScore     DECIMAL(5,4),
        @AssessmentScore  DECIMAL(5,4),
        @RecencyScore     DECIMAL(5,4),
        @ConsistencyScore DECIMAL(5,4),
        @NormStdDev       DECIMAL(5,4),
        @FinalConfidence  DECIMAL(3,2);

    -- SessionScore: min(distinct_sessions / 20, 1.0)
    SELECT @SessionCount = COUNT(DISTINCT SS.SessionId)
    FROM   StudySessions SS
    JOIN   Evidence      E  ON E.SessionId = SS.SessionId
    WHERE  SS.UserId  = @UserId
      AND  E.TopicId  = @TopicId;

    SET @SessionScore = CAST(
        CASE WHEN @SessionCount > 20 THEN 20 ELSE @SessionCount END
    AS DECIMAL(5,4)) / 20.0;

    -- AssessmentScore: min(assessment_count / 10, 1.0)
    SELECT @AssessCount = COUNT(*)
    FROM   Evidence E
    JOIN   StudySessions SS ON SS.SessionId = E.SessionId
    WHERE  SS.UserId  = @UserId
      AND  E.TopicId  = @TopicId
      AND  E.Type IN ('quiz', 'assessment', 'retention_test');

    SET @AssessmentScore = CAST(
        CASE WHEN @AssessCount > 10 THEN 10 ELSE @AssessCount END
    AS DECIMAL(5,4)) / 10.0;

    -- RecencyScore: exp(-DaysSinceLastStudy / 60)
    SELECT @DaysSince = ISNULL(DATEDIFF(day, MAX(E.CreatedAt), GETUTCDATE()), 999)
    FROM   Evidence      E
    JOIN   StudySessions SS ON SS.SessionId = E.SessionId
    WHERE  SS.UserId  = @UserId
      AND  E.TopicId  = @TopicId;

    SET @RecencyScore = CAST(EXP(-CAST(@DaysSince AS FLOAT) / 60.0) AS DECIMAL(5,4));

    -- ConsistencyScore: 1.0 - normalized_std_dev
    SELECT
        @StdDev   = ISNULL(STDEV(CAST(E.Score AS FLOAT)), 0),
        @AvgScore = ISNULL(AVG(CAST(E.Score AS FLOAT)), 0)
    FROM Evidence      E
    JOIN StudySessions SS ON SS.SessionId = E.SessionId
    WHERE SS.UserId  = @UserId
      AND E.TopicId  = @TopicId;

    SET @NormStdDev = CAST(
        CASE WHEN @AvgScore > 0 THEN @StdDev / @AvgScore ELSE 0 END
    AS DECIMAL(5,4));

    SET @ConsistencyScore = CAST(
        1.0 - CASE WHEN @NormStdDev > 1 THEN 1.0 ELSE @NormStdDev END
    AS DECIMAL(5,4));

    -- Composite confidence
    SET @FinalConfidence = ROUND(
        0.30 * @SessionScore
      + 0.30 * @AssessmentScore
      + 0.20 * @RecencyScore
      + 0.20 * @ConsistencyScore,
    2);

    -- Update UserTopicMastery
    UPDATE UserTopicMasteries
    SET Confidence  = @FinalConfidence,
        LastUpdated = GETUTCDATE()
    WHERE UserId = @UserId AND TopicId = @TopicId;

    SELECT
        @UserId           AS UserId,
        @TopicId          AS TopicId,
        @SessionScore     AS SessionScore,
        @AssessmentScore  AS AssessmentScore,
        @RecencyScore     AS RecencyScore,
        @ConsistencyScore AS ConsistencyScore,
        @FinalConfidence  AS FinalConfidence;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_ProcessSession] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Orchestrator SP: Runs full telemetry pipeline
-- 1. Compute EvidenceScore
-- 2. Update Mastery (EMA)
-- 3. Compute Confidence
-- 4. Recalculate Domain score
CREATE PROCEDURE [dbo].[usp_ProcessSession]
    @UserId        NVARCHAR(450),
    @SessionId     BIGINT,
    @TopicId       INT,
    @DomainTopicId INT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @EvidenceScore DECIMAL(3,2);

    -- Step 1: Compute evidence score
    EXEC usp_ComputeEvidenceScore
        @SessionId     = @SessionId,
        @TopicId       = @TopicId,
        @EvidenceScore = @EvidenceScore OUTPUT;

    -- Step 2: Update mastery
    EXEC usp_UpdateMastery
        @UserId        = @UserId,
        @TopicId       = @TopicId,
        @EvidenceScore = @EvidenceScore;

    -- Step 3: Compute confidence
    EXEC usp_ComputeConfidenceScore
        @UserId  = @UserId,
        @TopicId = @TopicId;

    -- Step 4: Recalculate domain score
    EXEC usp_RecalcDomainScore
        @UserId        = @UserId,
        @DomainTopicId = @DomainTopicId;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_GetRecommendationPriorities] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Returns top 20 topics by learning priority
-- Priority = (RequiredMastery - Mastery) * Confidence * Interest * CareerRelevance
CREATE PROCEDURE [dbo].[usp_GetRecommendationPriorities]
    @UserId          NVARCHAR(450),
    @RequiredMastery DECIMAL(3,2) = 0.80
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 20
        T.TopicId,
        T.Name        AS TopicName,
        T.Type,
        T.Difficulty,
        UTM.Mastery,
        UTM.Confidence,
        UTM.Interest,
        UTM.EvidenceCount,
        ISNULL(
            CAST(GoalCount.Cnt AS DECIMAL) / NULLIF(TotalGoals.Total, 0),
        0.50)         AS CareerRelevance,
        ROUND(
            (@RequiredMastery - UTM.Mastery)
            * UTM.Confidence
            * UTM.Interest
            * ISNULL(CAST(GoalCount.Cnt AS DECIMAL) / NULLIF(TotalGoals.Total, 0), 0.50),
        4)            AS Priority
    FROM UserTopicMasteries UTM
    JOIN Topics           T ON T.TopicId = UTM.TopicId
    OUTER APPLY (
        SELECT COUNT(DISTINCT G.GoalId) AS Cnt
        FROM   Goals  G
        JOIN   Topics DT ON DT.Type = 'Domain'
                         AND DT.Name LIKE '%' + T.Name + '%'
        WHERE  G.UserId = @UserId
    ) GoalCount
    CROSS JOIN (
        SELECT COUNT(*) AS Total FROM Goals WHERE UserId = @UserId
    ) TotalGoals
    WHERE UTM.UserId  = @UserId
      AND UTM.Mastery < @RequiredMastery
    ORDER BY Priority DESC;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_EnsureUserTopicMastery] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Creates UserTopicMastery record if it doesn't exist
CREATE PROCEDURE [dbo].[usp_EnsureUserTopicMastery]
    @UserId  NVARCHAR(450),
    @TopicId INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (
        SELECT 1 FROM UserTopicMasteries WHERE UserId = @UserId AND TopicId = @TopicId
    )
        INSERT INTO UserTopicMasteries (UserId, TopicId, Mastery, Confidence, Interest, EvidenceCount, LastUpdated)
        VALUES (@UserId, @TopicId, 0.00, 0.00, 0.50, 0, GETUTCDATE());
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_GetOrCreateResource] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Gets existing Resource by URL or creates new one
CREATE PROCEDURE [dbo].[usp_GetOrCreateResource]
    @Title              NVARCHAR(255),
    @Type               VARCHAR(50),
    @Url                VARCHAR(2048),
    @Difficulty         INT = 1,
    @Depth              INT = 1,
    @EstimatedMinutes   INT = 30,
    @ResourceId         BIGINT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    -- Check if resource exists
    SELECT @ResourceId = ResourceId
    FROM Resources
    WHERE Url = @Url;

    IF @ResourceId IS NULL
    BEGIN
        -- Create new resource
        INSERT INTO Resources (Title, Type, Url, Difficulty, Depth, EstimatedMinutes, CreatedAt)
        VALUES (@Title, @Type, @Url, @Difficulty, @Depth, @EstimatedMinutes, GETUTCDATE());

        SET @ResourceId = SCOPE_IDENTITY();
    END
    ELSE
    BEGIN
        -- Update existing resource
        UPDATE Resources
        SET Title = @Title, EstimatedMinutes = @EstimatedMinutes
        WHERE ResourceId = @ResourceId;
    END

    SELECT @ResourceId AS ResourceId;
END;
GO

/****** Object:  StoredProcedure [dbo].[usp_UpsertTopic] ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Creates or gets Topic by Name
CREATE PROCEDURE [dbo].[usp_UpsertTopic]
    @Name            NVARCHAR(150),
    @Description     NVARCHAR(MAX) = NULL,
    @Type            VARCHAR(50) = 'Concept',
    @Difficulty      INT = 1,
    @EstimatedHours  DECIMAL(5,2) = 4.00,
    @TopicId         INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    -- Check if topic exists
    SELECT @TopicId = TopicId FROM Topics WHERE Name = @Name;

    IF @TopicId IS NULL
    BEGIN
        -- Create new topic
        INSERT INTO Topics (Name, Description, Type, Difficulty, EstimatedHours)
        VALUES (@Name, @Description, @Type, @Difficulty, @EstimatedHours);

        SET @TopicId = SCOPE_IDENTITY();
    END

    SELECT @TopicId AS TopicId, @Name AS Name,
           CASE WHEN @@ROWCOUNT > 0 THEN 1 ELSE 0 END AS Created;
END;
GO
